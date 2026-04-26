"""
Prompt Weaver - 轻量级 Prompt 编排引擎

一个零依赖的 Prompt 编排工具，支持：
- 链式 prompt 调用
- 条件分支
- 变量替换
- 模板继承
- 错误处理与重试
- 并行执行
"""

import re
import json
import time
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field
from enum import Enum


class NodeType(Enum):
    """节点类型"""
    PROMPT = "prompt"
    CONDITION = "condition"
    TRANSFORM = "transform"
    OUTPUT = "output"
    LOOP = "loop"
    PARALLEL = "parallel"
    TRY_CATCH = "try_catch"
    SUBWORKFLOW = "subworkflow"
    MAP_REDUCE = "map_reduce"


@dataclass
class Node:
    """工作流节点"""
    id: str
    type: NodeType
    config: Dict[str, Any] = field(default_factory=dict)
    next: Optional[str] = None
    branches: Dict[str, str] = field(default_factory=dict)
    max_retries: int = 0
    retry_delay: float = 0
    on_error: Optional[Callable] = None


@dataclass
class Context:
    """执行上下文"""
    variables: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)
    current_output: Any = None
    errors: Dict[str, Exception] = field(default_factory=dict)
    parallel_results: Dict[str, List[Any]] = field(default_factory=dict)

    def set(self, key: str, value: Any):
        self.variables[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.variables.get(key, default)

    def push_history(self, node_id: str, output: Any):
        self.history.append({
            "node": node_id,
            "output": output
        })
        self.current_output = output

    def snapshot(self) -> Dict[str, Any]:
        """Capture current state for later restore."""
        import copy
        return {
            "variables": copy.deepcopy(self.variables),
            "history": copy.deepcopy(self.history),
            "current_output": copy.deepcopy(self.current_output) if isinstance(self.current_output, (list, dict)) else self.current_output,
            "errors": {k: str(v) for k, v in self.errors.items()},
            "parallel_results": copy.deepcopy(self.parallel_results),
        }

    def restore(self, snap: Dict[str, Any]):
        """Restore state from a previous snapshot."""
        import copy
        self.variables = copy.deepcopy(snap.get("variables", {}))
        self.history = copy.deepcopy(snap.get("history", []))
        self.current_output = snap.get("current_output")
        self.errors = {}
        self.parallel_results = copy.deepcopy(snap.get("parallel_results", {}))


@dataclass
class RunResult:
    """safe_run() 返回结果"""
    success: bool
    context: Optional[Context] = None
    error: Optional[Exception] = None


@dataclass
class NodeMetrics:
    """单个节点的执行指标"""
    node_id: str
    duration_ms: float = 0.0
    attempts: int = 0
    status: str = "success"  # success | error | skipped

@dataclass
class ExecutionMetrics:
    """完整工作流的执行指标"""
    total_duration_ms: float = 0.0
    nodes: List[NodeMetrics] = field(default_factory=list)
    start_time: float = 0.0

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def error_count(self) -> int:
        return sum(1 for n in self.nodes if n.status == "error")


HookFunc = Callable[[str, str, Context], None]  # (event, node_id, ctx) -> None


class PromptWeaver:
    """
    Prompt 编排引擎
    """

    def __init__(self, on_error: Optional[Callable[[str, Exception], None]] = None):
        self.nodes: Dict[str, Node] = {}
        self.start_node: Optional[str] = None
        self.transformers: Dict[str, Callable] = {}
        self.templates: Dict[str, str] = {}
        self.on_error = on_error
        self._template_cache: Dict[str, str] = {}
        self._hooks: List[HookFunc] = []
        self.metrics: Optional[ExecutionMetrics] = None
        self._register_default_transformers()

    def add_hook(self, hook: HookFunc) -> "PromptWeaver":
        """添加生命周期钩子。事件: before_node, after_node, on_error"""
        self._hooks.append(hook)
        return self

    def _emit(self, event: str, node_id: str, ctx: Context):
        for hook in self._hooks:
            try:
                hook(event, node_id, ctx)
            except Exception:
                pass  # hooks must not break execution

    def _register_default_transformers(self):
        """注册默认转换器"""
        self.transformers["upper"] = lambda x: x.upper() if isinstance(x, str) else x
        self.transformers["lower"] = lambda x: x.lower() if isinstance(x, str) else x
        self.transformers["trim"] = lambda x: x.strip() if isinstance(x, str) else x
        self.transformers["length"] = lambda x: len(x)
        self.transformers["json"] = lambda x: json.dumps(x, ensure_ascii=False)
        self.transformers["split"] = lambda x: x.split() if isinstance(x, str) else list(x)
        self.transformers["join"] = lambda x: " ".join(x) if isinstance(x, list) else str(x)
        self.transformers["first"] = lambda x: x[0] if x else None
        self.transformers["last"] = lambda x: x[-1] if x else None
        self.transformers["reverse"] = lambda x: x[::-1] if isinstance(x, (str, list)) else x
        self.transformers["sort"] = lambda x: sorted(x) if isinstance(x, list) else x
        self.transformers["head"] = lambda x: x[:5] if isinstance(x, (str, list)) else x
        self.transformers["tail"] = lambda x: x[-5:] if isinstance(x, (str, list)) else x
        self.transformers["splitlines"] = lambda x: x.splitlines() if isinstance(x, str) else x
        self.transformers["unique"] = lambda x: list(dict.fromkeys(x)) if isinstance(x, list) else x
        self.transformers["count"] = lambda x: len(x)
        self.transformers["default"] = lambda x: x if x else ""

    def register_transformer(self, name: str, func: Callable):
        """注册自定义转换器"""
        self.transformers[name] = func

    def register_template(self, name: str, template: str):
        """注册命名模板"""
        self.templates[name] = template

    # Alias
    add_template = register_template

    def _resolve_template(self, template: str) -> str:
        """解析模板继承/包含指令，返回最终模板（带缓存）"""
        if template in self._template_cache:
            return self._template_cache[template]
        # Handle {% include "name" %}
        def replace_includes(tmpl):
            pattern = r'\{%\s*include\s+"(\w+)"\s*%\}'
            def replacer(m):
                name = m.group(1)
                if name not in self.templates:
                    raise ValueError(f"Template '{name}' not found for include")
                return self.templates[name]
            return re.sub(pattern, replacer, tmpl)

        # Handle {% extends "base" %} and {% block name %}...{% endblock %}
        extends_match = re.match(r'\{%\s*extends\s+"(\w+)"\s*%\}(.*)', template, re.DOTALL)
        if extends_match:
            parent_name = extends_match.group(1)
            child_content = extends_match.group(2)
            if parent_name not in self.templates:
                raise ValueError(f"Parent template '{parent_name}' not found")
            parent = self.templates[parent_name]

            # Extract blocks from child
            child_blocks = {}
            block_pattern = r'\{%\s*block\s+(\w+)\s*%\}(.*?)\{%\s*endblock\s*%\}'
            for m in re.finditer(block_pattern, child_content, re.DOTALL):
                child_blocks[m.group(1)] = m.group(2)

            # Replace blocks in parent
            def block_replacer(m):
                block_name = m.group(1)
                return child_blocks.get(block_name, m.group(2))

            result = re.sub(block_pattern, block_replacer, parent, flags=re.DOTALL)
            resolved = replace_includes(result)
            self._template_cache[template] = resolved
            return resolved

        resolved = replace_includes(template)
        self._template_cache[template] = resolved
        return resolved

    def add_prompt(self, node_id: str, template: str, next_node: Optional[str] = None,
                   max_retries: int = 0, retry_delay: float = 0,
                   on_error: Optional[Callable] = None) -> "PromptWeaver":
        """添加 prompt 节点"""
        if not self.start_node:
            self.start_node = node_id
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.PROMPT,
            config={"template": template},
            next=next_node,
            max_retries=max_retries,
            retry_delay=retry_delay,
            on_error=on_error,
        )
        return self

    def add_condition(
        self,
        node_id: str,
        condition: Union[Callable[[Context], bool], str],
        true_branch: str,
        false_branch: str
    ) -> "PromptWeaver":
        """添加条件分支节点"""
        if not self.start_node:
            self.start_node = node_id

        condition_expr = None
        if isinstance(condition, str):
            condition_expr = condition
            condition = self._parse_condition(condition)

        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.CONDITION,
            config={"condition": condition, "condition_expr": condition_expr},
            branches={"true": true_branch, "false": false_branch}
        )
        return self

    def add_transform(
        self,
        node_id: str,
        transforms: List[str],
        next_node: Optional[str] = None
    ) -> "PromptWeaver":
        """添加转换节点"""
        if not self.start_node:
            self.start_node = node_id
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.TRANSFORM,
            config={"transforms": transforms},
            next=next_node
        )
        return self

    def add_output(self, node_id: str, output_key: str = "result") -> "PromptWeaver":
        """添加输出节点"""
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.OUTPUT,
            config={"key": output_key}
        )
        return self

    def add_loop(self, node_id: str, loop_type: str, config: Dict[str, Any],
                 next_node: Optional[str] = None) -> "PromptWeaver":
        """添加循环节点"""
        if not self.start_node:
            self.start_node = node_id
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.LOOP,
            config={"type": loop_type, **config},
            next=next_node
        )
        return self

    def add_parallel(
        self,
        node_id: str,
        branches: List[str],
        merge_strategy: Union[str, Callable] = "join",
        next_node: Optional[str] = None
    ) -> "PromptWeaver":
        """添加并行执行节点"""
        if not self.start_node:
            self.start_node = node_id
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.PARALLEL,
            config={"branches": branches, "merge_strategy": merge_strategy},
            next=next_node
        )
        return self

    def add_try_catch(
        self,
        node_id: str,
        try_node: str,
        catch_node: str,
        next_node: Optional[str] = None
    ) -> "PromptWeaver":
        """添加 try-catch 错误处理节点"""
        if not self.start_node:
            self.start_node = node_id
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.TRY_CATCH,
            config={"try_node": try_node, "catch_node": catch_node},
            next=next_node
        )
        return self

    def add_subworkflow(
        self,
        node_id: str,
        workflow: "PromptWeaver",
        input_mapping: Optional[Dict[str, str]] = None,
        output_key: Optional[str] = None,
        next_node: Optional[str] = None
    ) -> "PromptWeaver":
        """添加子工作流节点 - 嵌套调用另一个 PromptWeaver 实例"""
        if not self.start_node:
            self.start_node = node_id
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.SUBWORKFLOW,
            config={
                "workflow": workflow,
                "input_mapping": input_mapping or {},
                "output_key": output_key,
            },
            next=next_node
        )
        return self

    def add_map_reduce(
        self,
        node_id: str,
        items_expr: str,
        variable_name: str,
        map_template: str,
        reduce_strategy: Union[str, Callable] = "join",
        next_node: Optional[str] = None
    ) -> "PromptWeaver":
        """添加 Map-Reduce 节点"""
        if not self.start_node:
            self.start_node = node_id
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.MAP_REDUCE,
            config={
                "items_expr": items_expr,
                "variable_name": variable_name,
                "map_template": map_template,
                "reduce_strategy": reduce_strategy,
            },
            next=next_node
        )
        return self

    def add_refine(
        self,
        node_id: str,
        template: str,
        max_iterations: int = 5,
        convergence_check: Optional[Callable[[str, str], bool]] = None,
        next_node: Optional[str] = None
    ) -> "PromptWeaver":
        """
        添加迭代优化节点 - 反复渲染模板直到收敛或达到最大迭代次数。
        
        模板中可用 {{_prev_output}} 访问上一次的输出。
        收敛检查函数接收 (prev_output, current_output) 返回 bool。
        默认收敛条件：输出不再变化。
        """
        if not self.start_node:
            self.start_node = node_id
        self.nodes[node_id] = Node(
            id=node_id,
            type=NodeType.LOOP,
            config={
                "type": "refine",
                "template": template,
                "max_iterations": max_iterations,
                "convergence_check": convergence_check,
            },
            next=next_node
        )
        return self

    def _parse_condition(self, expr: str) -> Callable[[Context], bool]:
        """解析条件表达式"""
        def evaluator(ctx: Context) -> bool:
            evaluated = self._render_template(expr, ctx.variables)
            if " contains " in evaluated:
                parts = evaluated.split(" contains ")
                return parts[1].strip("'\"") in parts[0].strip("'\"")
            elif " >= " in evaluated:
                parts = evaluated.split(" >= ")
                return float(parts[0].strip("'\"")) >= float(parts[1].strip("'\""))
            elif " <= " in evaluated:
                parts = evaluated.split(" <= ")
                return float(parts[0].strip("'\"")) <= float(parts[1].strip("'\""))
            elif " > " in evaluated:
                parts = evaluated.split(" > ")
                return float(parts[0].strip("'\"")) > float(parts[1].strip("'\""))
            elif " < " in evaluated:
                parts = evaluated.split(" < ")
                return float(parts[0].strip("'\"")) < float(parts[1].strip("'\""))
            elif " == " in evaluated:
                parts = evaluated.split(" == ")
                return parts[0].strip("'\"") == parts[1].strip("'\"")
            elif " != " in evaluated:
                parts = evaluated.split(" != ")
                return parts[0].strip("'\"") != parts[1].strip("'\"")
            else:
                return bool(evaluated and evaluated != "False" and evaluated != "0")
        return evaluator

    def _render_template(self, template: str, variables: Dict[str, Any]) -> str:
        """渲染模板，替换 {{var}} 变量"""
        # Resolve inheritance/includes first
        template = self._resolve_template(template)

        result = template

        # Handle {{var | filter}}
        pattern_with_filter = r'\{\{\s*(\w+)\s*\|\s*(\w+)\s*\}\}'
        def replace_with_filter(match):
            var_name = match.group(1)
            filter_name = match.group(2)
            value = variables.get(var_name, "")
            if filter_name in self.transformers:
                value = self.transformers[filter_name](value)
            return str(value)
        result = re.sub(pattern_with_filter, replace_with_filter, result)

        # Handle {{var}}
        pattern_simple = r'\{\{\s*(\w+)\s*\}\}'
        def replace_simple(match):
            var_name = match.group(1)
            return str(variables.get(var_name, ""))
        result = re.sub(pattern_simple, replace_simple, result)

        return result

    def _execute_node_with_retry(self, node: Node, ctx: Context) -> Optional[str]:
        """Execute a node with retry support, metrics, and hooks"""
        retries = node.max_retries
        delay = node.retry_delay
        last_exc = None
        attempts = 0
        t0 = time.monotonic()
        self._emit("before_node", node.id, ctx)

        for attempt in range(retries + 1):
            try:
                result = self._execute_node(node, ctx)
                attempts = attempt + 1
                # Record metrics
                if self.metrics is not None:
                    dur = (time.monotonic() - t0) * 1000
                    self.metrics.nodes.append(NodeMetrics(node.id, dur, attempts, "success"))
                self._emit("after_node", node.id, ctx)
                return result
            except Exception as e:
                last_exc = e
                attempts = attempt + 1
                self._emit("on_error", node.id, ctx)
                if node.on_error:
                    node.on_error(node.id, e)
                if self.on_error:
                    self.on_error(node.id, e)
                if attempt < retries and delay > 0:
                    time.sleep(delay)

        if self.metrics is not None:
            dur = (time.monotonic() - t0) * 1000
            self.metrics.nodes.append(NodeMetrics(node.id, dur, attempts, "error"))
        raise last_exc

    def _execute_node(self, node: Node, ctx: Context) -> Optional[str]:
        """执行单个节点，返回下一个节点 ID"""

        if node.type == NodeType.PROMPT:
            output = self._render_template(node.config["template"], ctx.variables)
            ctx.push_history(node.id, output)
            return node.next

        elif node.type == NodeType.CONDITION:
            condition = node.config["condition"]
            result = condition(ctx)
            ctx.push_history(node.id, result)
            return node.branches["true" if result else "false"]

        elif node.type == NodeType.TRANSFORM:
            output = ctx.current_output
            for transform_name in node.config["transforms"]:
                if transform_name in self.transformers:
                    output = self.transformers[transform_name](output)
            ctx.push_history(node.id, output)
            return node.next

        elif node.type == NodeType.OUTPUT:
            ctx.set(node.config.get("key", "result"), ctx.current_output)
            ctx.push_history(node.id, ctx.current_output)
            return None

        elif node.type == NodeType.LOOP:
            return self._execute_loop(node, ctx)

        elif node.type == NodeType.PARALLEL:
            return self._execute_parallel(node, ctx)

        elif node.type == NodeType.TRY_CATCH:
            return self._execute_try_catch(node, ctx)

        elif node.type == NodeType.SUBWORKFLOW:
            return self._execute_subworkflow(node, ctx)

        elif node.type == NodeType.MAP_REDUCE:
            return self._execute_map_reduce(node, ctx)

        return None

    def _execute_try_catch(self, node: Node, ctx: Context) -> Optional[str]:
        """执行 try-catch 节点"""
        try_node_id = node.config["try_node"]
        catch_node_id = node.config["catch_node"]

        try:
            # Execute try branch
            current = try_node_id
            max_iter = 50
            iters = 0
            while current and iters < max_iter:
                if current not in self.nodes:
                    break
                n = self.nodes[current]
                current = self._execute_node_with_retry(n, ctx)
                iters += 1
            return node.next
        except Exception as e:
            ctx.errors[try_node_id] = e
            if self.on_error:
                self.on_error(try_node_id, e)
            # Execute catch branch
            current = catch_node_id
            max_iter = 50
            iters = 0
            while current and iters < max_iter:
                if current not in self.nodes:
                    break
                n = self.nodes[current]
                current = self._execute_node_with_retry(n, ctx)
                iters += 1
            return node.next

    def _execute_parallel(self, node: Node, ctx: Context) -> Optional[str]:
        """执行并行节点（顺序执行各分支，语义上标记为并行）"""
        branch_ids = node.config["branches"]
        merge_strategy = node.config["merge_strategy"]

        results = []
        saved_output = ctx.current_output

        for branch_id in branch_ids:
            # Reset current_output for each branch so they start from same state
            ctx.current_output = saved_output
            current = branch_id
            max_iter = 50
            iters = 0
            while current and iters < max_iter:
                if current not in self.nodes:
                    break
                n = self.nodes[current]
                current = self._execute_node_with_retry(n, ctx)
                iters += 1
                # Stop at output nodes or nodes that go to None
                if current is None:
                    break
            results.append(ctx.current_output)

        ctx.parallel_results[node.id] = results

        # Apply merge strategy
        if callable(merge_strategy):
            merged = merge_strategy(results)
        elif merge_strategy == "first":
            merged = results[0] if results else None
        elif merge_strategy == "last":
            merged = results[-1] if results else None
        else:  # "join"
            merged = "\n".join(str(r) for r in results if r is not None)

        ctx.push_history(node.id, merged)
        return node.next

    def _execute_loop(self, node: Node, ctx: Context) -> Optional[str]:
        """执行循环节点"""
        loop_type = node.config["type"]

        if loop_type == "while":
            return self._execute_while_loop(node, ctx)
        elif loop_type == "for":
            return self._execute_for_loop(node, ctx)
        elif loop_type == "refine":
            return self._execute_refine(node, ctx)
        else:
            raise ValueError(f"Unsupported loop type: {loop_type}")

    def _execute_while_loop(self, node: Node, ctx: Context) -> Optional[str]:
        """执行 while 循环 - 最多迭代 max_iterations 次"""
        condition_expr = node.config.get("condition", "")
        max_iter = node.config.get("max_iterations", 100)
        body_node = node.config.get("body_node")
        counter_var = node.config.get("counter")
        max_count = node.config.get("max_count", 10)

        iteration_key = f"_loop_{node.id}_iter"
        current_iter = ctx.get(iteration_key, 0)

        if counter_var:
            val = ctx.get(counter_var, 0)
            if isinstance(val, (int, float)) and val >= max_count:
                ctx.set(iteration_key, 0)
                return node.next
        elif current_iter >= max_iter:
            ctx.set(iteration_key, 0)
            return node.next

        if condition_expr:
            condition = self._parse_condition(condition_expr)
            if not condition(ctx):
                ctx.set(iteration_key, 0)
                return node.next

        ctx.set(iteration_key, current_iter + 1)

        if counter_var:
            ctx.set(counter_var, ctx.get(counter_var, 0) + 1)

        if body_node:
            return body_node
        return node.id

    def _execute_for_loop(self, node: Node, ctx: Context) -> Optional[str]:
        """执行 for 循环 - 完整迭代并累积结果"""
        variable_name = node.config["variable"]
        items_expr = node.config["items"]
        body_template = node.config["body"]

        # Resolve items
        if isinstance(items_expr, str):
            if items_expr.startswith("{{") and items_expr.endswith("}}"):
                var_name = items_expr[2:-2].strip()
                items = ctx.get(var_name, [])
            else:
                try:
                    items = eval(items_expr)
                except Exception:
                    items = []
        else:
            items = items_expr

        outputs = []
        for item in items:
            ctx.set(variable_name, item)
            output = self._render_template(body_template, ctx.variables)
            outputs.append(output)

        # Join accumulated results
        result = "\n".join(outputs)
        ctx.push_history(node.id, result)
        return node.next

    def _execute_refine(self, node: Node, ctx: Context) -> Optional[str]:
        """执行迭代优化 - 反复渲染直到收敛"""
        template = node.config["template"]
        max_iter = node.config.get("max_iterations", 5)
        check = node.config.get("convergence_check")

        prev = str(ctx.current_output) if ctx.current_output is not None else ""

        for i in range(max_iter):
            ctx.set("_prev_output", prev)
            ctx.set("_iteration", i + 1)
            current = self._render_template(template, ctx.variables)

            if check:
                if check(prev, current):
                    ctx.push_history(node.id, current)
                    return node.next
            elif current == prev and i > 0:
                ctx.push_history(node.id, current)
                return node.next

            prev = current

        ctx.push_history(node.id, prev)
        return node.next

    def _execute_subworkflow(self, node: Node, ctx: Context) -> Optional[str]:
        """执行子工作流节点"""
        workflow = node.config["workflow"]
        input_mapping = node.config.get("input_mapping", {})
        output_key = node.config.get("output_key")

        sub_vars = {}
        for target_key, source_key in input_mapping.items():
            sub_vars[target_key] = ctx.get(source_key)
        sub_vars["input"] = ctx.current_output

        sub_ctx = workflow.run(sub_vars)
        result = sub_ctx.current_output

        if output_key:
            ctx.set(output_key, result)

        ctx.push_history(node.id, result)
        return node.next

    def _execute_map_reduce(self, node: Node, ctx: Context) -> Optional[str]:
        """执行 Map-Reduce 节点"""
        items_expr = node.config["items_expr"]
        variable_name = node.config["variable_name"]
        map_template = node.config["map_template"]
        reduce_strategy = node.config["reduce_strategy"]

        if items_expr.startswith("{{") and items_expr.endswith("}}"):
            var_name = items_expr[2:-2].strip()
            items = ctx.get(var_name, [])
        else:
            items = ctx.get(items_expr, [])

        mapped = []
        for item in items:
            sub_vars = dict(ctx.variables)
            sub_vars[variable_name] = item
            result = self._render_template(map_template, sub_vars)
            mapped.append(result)

        if callable(reduce_strategy):
            reduced = reduce_strategy(mapped)
        elif reduce_strategy == "concat":
            reduced = "".join(mapped)
        elif reduce_strategy == "sum":
            reduced = sum(float(x) for x in mapped)
        elif reduce_strategy == "first":
            reduced = mapped[0] if mapped else None
        elif reduce_strategy == "last":
            reduced = mapped[-1] if mapped else None
        else:
            reduced = "\n".join(mapped)

        ctx.push_history(node.id, reduced)
        return node.next

    def validate(self) -> Dict[str, Any]:
        """Validate pipeline integrity. Returns {valid, errors, warnings}."""
        errors = []
        warnings = []

        if not self.start_node:
            errors.append("No start node defined")
        elif self.start_node not in self.nodes:
            errors.append(f"Start node '{self.start_node}' not found in nodes")

        # Check all next/branch targets exist
        for nid, node in self.nodes.items():
            if node.next and node.next not in self.nodes:
                errors.append(f"Node '{nid}' references missing next node '{node.next}'")
            for branch, target in node.branches.items():
                if target and target not in self.nodes:
                    errors.append(f"Node '{nid}' branch '{branch}' references missing node '{target}'")

        # Check for unreachable nodes
        if self.start_node and self.start_node in self.nodes:
            reachable = set()
            stack = [self.start_node]
            while stack:
                cur = stack.pop()
                if cur in reachable or cur not in self.nodes:
                    continue
                reachable.add(cur)
                node = self.nodes[cur]
                if node.next:
                    stack.append(node.next)
                for target in node.branches.values():
                    if target:
                        stack.append(target)
            for nid in self.nodes:
                if nid not in reachable:
                    warnings.append(f"Node '{nid}' is unreachable")

        # Check condition nodes have both branches
        for nid, node in self.nodes.items():
            if node.type == NodeType.CONDITION:
                if not node.branches.get("true"):
                    warnings.append(f"Condition node '{nid}' missing 'true' branch")
                if not node.branches.get("false"):
                    warnings.append(f"Condition node '{nid}' missing 'false' branch")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    def dry_run(self, variables: Optional[Dict[str, Any]] = None) -> List[str]:
        """Trace execution path without evaluating templates. Returns list of node IDs."""
        if not self.start_node:
            raise ValueError("No start node defined")
        path = []
        current = self.start_node
        visited = set()
        while current and current in self.nodes:
            if current in visited:
                path.append(f"{current} (CYCLE)")
                break
            visited.add(current)
            path.append(current)
            node = self.nodes[current]
            if node.type == NodeType.CONDITION:
                # Try to evaluate condition statically if it's a simple expr
                cond_expr = node.config.get("condition_expr", "")
                if cond_expr:
                    try:
                        cond = self._parse_condition(cond_expr)
                        ctx = Context(variables=variables or {})
                        result = cond(ctx)
                        current = node.branches.get(str(result).lower(), node.branches.get("true"))
                    except Exception:
                        path.append("  (condition cannot be evaluated statically)")
                        break
                else:
                    break
            else:
                current = node.next
        return path

    def merge(self, other: "PromptWeaver", prefix: str = "") -> "PromptWeaver":
        """Merge another pipeline's nodes/templates/transformers into this one.
        Optionally prefix node IDs to avoid collisions. Returns self."""
        p = prefix
        # Merge nodes
        for nid, node in other.nodes.items():
            new_id = f"{p}{nid}"
            new_node = Node(
                id=new_id,
                type=node.type,
                config=dict(node.config),
                next=f"{p}{node.next}" if node.next and p else node.next,
                branches={k: f"{p}{v}" if v and p else v for k, v in node.branches.items()},
                max_retries=node.max_retries,
                retry_delay=node.retry_delay,
                on_error=node.on_error,
            )
            self.nodes[new_id] = new_node
        # Merge templates
        for name, tmpl in other.templates.items():
            self.templates[f"{p}{name}"] = tmpl
        # Merge transformers
        for name, func in other.transformers.items():
            self.transformers[f"{p}{name}"] = func
        # If we have no start_node, adopt other's
        if not self.start_node and other.start_node:
            self.start_node = f"{p}{other.start_node}"
        return self

    def pipeline_stats(self) -> dict:
        """Return pipeline structure statistics."""
        node_types = {}
        for n in self.nodes.values():
            t = n.type.value if hasattr(n.type, 'value') else str(n.type)
            node_types[t] = node_types.get(t, 0) + 1
        return {
            "nodes": len(self.nodes),
            "node_types": node_types,
            "transformers": len(self.transformers),
            "templates": len(self.templates),
            "hooks": len(self._hooks),
            "has_start": self.start_node is not None,
        }

    def run(self, variables: Optional[Dict[str, Any]] = None) -> Context:
        """执行工作流"""
        ctx = Context(variables=variables or {})
        self.metrics = ExecutionMetrics()
        self.metrics.start_time = time.monotonic()

        if not self.start_node:
            raise ValueError("No start node defined")

        current = self.start_node
        max_iterations = 1000
        iterations = 0

        while current and iterations < max_iterations:
            if current not in self.nodes:
                raise ValueError(f"Node not found: {current}")

            node = self.nodes[current]
            current = self._execute_node_with_retry(node, ctx)
            iterations += 1

        if iterations >= max_iterations:
            raise RuntimeError("Max iterations exceeded - possible infinite loop")

        self.metrics.total_duration_ms = (time.monotonic() - self.metrics.start_time) * 1000
        return ctx

    def safe_run(self, variables: Optional[Dict[str, Any]] = None) -> RunResult:
        """安全执行工作流，返回结果对象而非抛出异常"""
        try:
            ctx = self.run(variables)
            return RunResult(success=True, context=ctx)
        except Exception as e:
            return RunResult(success=False, error=e)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "PromptWeaver":
        """从 YAML 配置创建（不依赖 PyYAML，使用简单解析）"""
        weaver = cls()
        lines = yaml_content.strip().split("\n")
        current_node = None
        current_config = {}
        in_config = False

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if stripped.startswith("- id:"):
                if current_node:
                    weaver._add_node_from_config(current_node, current_config)
                current_node = stripped.split(":", 1)[1].strip()
                current_config = {}
                in_config = True
            elif in_config and ":" in stripped:
                key, value = stripped.split(":", 1)
                key = key.strip()
                value = value.strip()
                if value.startswith("[") and value.endswith("]"):
                    value = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
                elif value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                elif value.isdigit():
                    value = int(value)
                current_config[key] = value

        if current_node:
            weaver._add_node_from_config(current_node, current_config)

        return weaver

    def _add_node_from_config(self, node_id: str, config: Dict[str, Any]):
        """从配置添加节点"""
        node_type = config.get("type", "prompt")

        if node_type == "prompt":
            self.add_prompt(node_id, config.get("template", ""), config.get("next"))
        elif node_type == "condition":
            self.add_condition(node_id, config.get("condition", lambda ctx: True),
                               config.get("true"), config.get("false"))
        elif node_type == "transform":
            self.add_transform(node_id, config.get("transforms", []), config.get("next"))
        elif node_type == "output":
            self.add_output(node_id, config.get("key", "result"))
        elif node_type == "loop":
            self.add_loop(node_id, config.get("type", "while"),
                          config.get("config", {}), config.get("next"))

    def to_dict(self) -> Dict[str, Any]:
        """将工作流序列化为字典（JSON-compatible）"""
        nodes = []
        for nid, node in self.nodes.items():
            n = {"id": nid, "type": node.type.value}
            if node.next:
                n["next"] = node.next
            if node.branches:
                n["branches"] = dict(node.branches)
            # Config (skip non-serializable callables, prefer expr if available)
            config = {}
            for k, v in node.config.items():
                if k == "condition" and "condition_expr" in node.config:
                    continue  # Skip callable, use condition_expr
                if k == "condition_expr" and v is not None:
                    config["condition"] = v
                    continue
                if callable(v):
                    continue
                config[k] = v
            if config:
                n["config"] = config
            if node.max_retries:
                n["max_retries"] = node.max_retries
            if node.retry_delay:
                n["retry_delay"] = node.retry_delay
            nodes.append(n)
        data = {"start_node": self.start_node, "nodes": nodes}
        if self.templates:
            data["templates"] = dict(self.templates)
        return data

    def to_json(self, indent: int = 2) -> str:
        """将工作流导出为 JSON 字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptWeaver":
        """从字典反序列化创建工作流"""
        weaver = cls()
        weaver.start_node = data.get("start_node")

        # Restore templates
        for name, tmpl in data.get("templates", {}).items():
            weaver.register_template(name, tmpl)

        # Restore nodes
        for n in data.get("nodes", []):
            nid = n["id"]
            ntype = n["type"]
            config = n.get("config", {})
            next_node = n.get("next")
            retries = n.get("max_retries", 0)
            delay = n.get("retry_delay", 0)

            if ntype == "prompt":
                weaver.add_prompt(nid, config.get("template", ""), next_node,
                                  max_retries=retries, retry_delay=delay)
            elif ntype == "condition":
                cond_str = config.get("condition", "")
                if cond_str:
                    cond = weaver._parse_condition(cond_str)
                else:
                    cond = lambda ctx: True
                branches = n.get("branches", {})
                weaver.add_condition(nid, cond, branches.get("true"), branches.get("false"))
            elif ntype == "transform":
                weaver.add_transform(nid, config.get("transforms", []), next_node)
            elif ntype == "output":
                weaver.add_output(nid, config.get("key", "result"))
            elif ntype == "loop":
                weaver.add_loop(nid, config.get("type", "while"), config, next_node)
            elif ntype == "parallel":
                weaver.add_parallel(nid, config.get("branches", []),
                                    config.get("merge_strategy", "join"), next_node)
            elif ntype == "try_catch":
                weaver.add_try_catch(nid, config.get("try_node"), config.get("catch_node"), next_node)
            elif ntype == "map_reduce":
                weaver.add_map_reduce(nid, config.get("items_expr", ""),
                                      config.get("variable_name", "item"),
                                      config.get("map_template", ""),
                                      config.get("reduce_strategy", "join"), next_node)
        return weaver

    @classmethod
    def from_json(cls, json_str: str) -> "PromptWeaver":
        """从 JSON 字符串导入工作流"""
        return cls.from_dict(json.loads(json_str))

    def to_mermaid(self) -> str:
        """生成 Mermaid 流程图"""
        lines = ["graph TD"]

        for node_id, node in self.nodes.items():
            if node.type == NodeType.PROMPT:
                label = node.config.get("template", "")[:30]
                lines.append(f'    {node_id}["{label}..."]')
            elif node.type == NodeType.CONDITION:
                lines.append(f'    {node_id}{{{node_id}}}')
            elif node.type == NodeType.TRANSFORM:
                lines.append(f'    {node_id}[[{node_id}]]')
            elif node.type == NodeType.OUTPUT:
                lines.append(f'    {node_id}(({node_id}))')
            elif node.type == NodeType.LOOP:
                lt = node.config.get("type", "while")
                lines.append(f'    {node_id}{{{{{lt} loop}}}}')
            elif node.type == NodeType.PARALLEL:
                lines.append(f'    {node_id}[/{node_id}/]')
            elif node.type == NodeType.TRY_CATCH:
                lines.append(f'    {node_id}[{node_id}]')

        for node_id, node in self.nodes.items():
            if node.next:
                lines.append(f'    {node_id} --> {node.next}')
            elif node.branches:
                for branch, target in node.branches.items():
                    lines.append(f'    {node_id} -->|{branch}| {target}')

        return "\n".join(lines)


class Chain:
    """
    链式 API 构建器
    """

    def __init__(self):
        self._weaver = PromptWeaver()
        self._counter = 0
        self._last_node = None

    def _next_id(self) -> str:
        self._counter += 1
        return f"node_{self._counter}"

    def prompt(self, template: str) -> "Chain":
        node_id = self._next_id()
        if self._last_node:
            self._weaver.nodes[self._last_node].next = node_id
        self._weaver.add_prompt(node_id, template)
        self._last_node = node_id
        return self

    def condition(self, cond: Union[Callable, str], true_template: str, false_template: str) -> "Chain":
        cond_id = self._next_id()
        true_id = self._next_id()
        false_id = self._next_id()

        if self._last_node:
            self._weaver.nodes[self._last_node].next = cond_id

        self._weaver.add_condition(cond_id, cond, true_id, false_id)
        self._weaver.add_prompt(true_id, true_template)
        self._weaver.add_prompt(false_id, false_template)

        self._last_node = None
        return self

    def transform(self, *transforms: str) -> "Chain":
        node_id = self._next_id()
        if self._last_node:
            self._weaver.nodes[self._last_node].next = node_id
        self._weaver.add_transform(node_id, list(transforms))
        self._last_node = node_id
        return self

    def output(self, key: str = "result") -> "Chain":
        node_id = self._next_id()
        if self._last_node:
            self._weaver.nodes[self._last_node].next = node_id
        self._weaver.add_output(node_id, key)
        self._last_node = node_id
        return self

    def run(self, variables: Optional[Dict[str, Any]] = None) -> Context:
        return self._weaver.run(variables)

    def to_mermaid(self) -> str:
        return self._weaver.to_mermaid()


def weave(template: str, variables: Optional[Dict[str, Any]] = None) -> str:
    """快速渲染单个模板"""
    weaver = PromptWeaver()
    weaver.add_prompt("start", template)
    weaver.add_output("end")
    weaver.nodes["start"].next = "end"
    ctx = weaver.run(variables)
    return ctx.current_output


def weave_file(path: str, variables: Optional[Dict[str, Any]] = None) -> str:
    """Load template from file and render with variables."""
    with open(path, "r", encoding="utf-8") as f:
        template = f.read()
    return weave(template, variables)
