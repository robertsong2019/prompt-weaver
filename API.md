# API 参考 🧵

> Prompt Weaver v0.3.0 完整 API 文档

## 目录

- [核心类](#核心类)
  - [PromptWeaver](#promptweaver)
  - [Chain](#chain)
  - [Context](#context)
- [数据类](#数据类)
  - [Node](#node)
  - [NodeType](#nodetype)
  - [RunResult](#runresult)
  - [NodeMetrics](#nodemetrics)
  - [ExecutionMetrics](#executionmetrics)
- [便捷函数](#便捷函数)
- [CLI](#cli)

---

## 核心类

### PromptWeaver

主引擎类，用于构建和执行 Prompt 工作流。

```python
from weaver import PromptWeaver

pw = PromptWeaver(on_error=None)
```

**参数：**
- `on_error` `(Callable[[str, Exception], None] | None)` — 全局错误回调，接收 `(node_id, exception)`

#### 添加节点

##### `add_prompt(node_id, template, next_node=None, max_retries=0, retry_delay=0)`

添加一个模板渲染节点。

| 参数 | 类型 | 说明 |
|------|------|------|
| `node_id` | `str` | 节点唯一标识 |
| `template` | `str` | Jinja-like 模板字符串，支持 `{{var}}` 和过滤器 `{{var \| upper}}` |
| `next_node` | `str \| None` | 下一个节点 ID，`None` 表示结束 |
| `max_retries` | `int` | 最大重试次数（默认 0） |
| `retry_delay` | `float` | 重试间隔秒数（默认 0） |

返回 `self`，支持链式调用。

##### `add_condition(node_id, condition, true_node, false_node, *, max_retries=0)`

添加条件分支节点。

| 参数 | 类型 | 说明 |
|------|------|------|
| `condition` | `Callable[[Context], bool] \| str` | 判断条件。函数接收 Context；字符串支持 `{{score}} >= 60` 表达式 |
| `true_node` | `str` | 条件为真时跳转的节点 |
| `false_node` | `str` | 条件为假时跳转的节点 |

##### `add_transform(node_id, transforms, next_node=None, max_retries=0, retry_delay=0)`

添加数据转换节点。

| 参数 | 类型 | 说明 |
|------|------|------|
| `transforms` | `str \| List[str]` | 转换器名称或名称列表，按顺序管道执行 |

##### `add_output(node_id, output_key="result")`

添加输出节点，将当前结果写入上下文变量。

##### `add_loop(node_id, loop_type, config, next_node=None, max_retries=0)`

添加循环节点。

| `loop_type` | 说明 | `config` 参数 |
|-------------|------|--------------|
| `"count"` | 固定次数 | `{"count": int}` |
| `"while"` | 条件循环 | `{"condition": Callable \| str}` |
| `"for"` | 遍历列表 | `{"items": str \| list, "var": str}` |

- `for` 循环的 `items` 可以是变量名（字符串）或直接传列表
- `var` 指定循环变量名，模板中用 `{{var}}` 访问

##### `add_parallel(node_id, branches, next_node=None)`

添加并行执行节点，多个分支同时运行后合并结果。

| 参数 | 类型 | 说明 |
|------|------|------|
| `branches` | `Dict[str, str]` | `{分支名: 节点ID}` 映射 |

结果存储在 `ctx.parallel_results[node_id]` 列表中。

##### `add_try_catch(node_id, try_node, catch_node, next_node=None)`

添加错误处理节点。

| 参数 | 类型 | 说明 |
|------|------|------|
| `try_node` | `str` | 尝试执行的节点 ID |
| `catch_node` | `str` | 出错时执行的节点 ID |

##### `add_subworkflow(node_id, workflow, input_mapping=None, output_key=None, next_node=None)`

嵌套调用另一个 PromptWeaver 工作流。

| 参数 | 类型 | 说明 |
|------|------|------|
| `workflow` | `PromptWeaver` | 子工作流实例 |
| `input_mapping` | `Dict[str, str] \| None` | 输入变量映射 `{子工作流变量: 父上下文变量}` |
| `output_key` | `str \| None` | 子工作流结果写入的变量名 |

##### `add_map_reduce(node_id, items_expr, item_var, map_template, reduce_strategy="join", next_node=None)`

批量处理模式。

| 参数 | 类型 | 说明 |
|------|------|------|
| `items_expr` | `str` | 变量名，引用上下文中的列表 |
| `item_var` | `str` | 循环变量名 |
| `map_template` | `str` | 每个元素的模板 |
| `reduce_strategy` | `str \| Callable` | 合并策略：`join`/`concat`/`sum`/`first`/`last`/自定义函数 |

##### `add_refine(node_id, template, max_rounds=3, convergence_fn=None, next_node=None)`

迭代优化节点，反复渲染模板直到收敛。

| 参数 | 类型 | 说明 |
|------|------|------|
| `template` | `str` | 每轮渲染的模板 |
| `max_rounds` | `int` | 最大迭代轮数（默认 3） |
| `convergence_fn` | `Callable[[Any, Any], bool] \| None` | 收敛判断函数 `(prev, curr) → bool`，返回 True 停止 |

#### 注册扩展

##### `register_transformer(name, func)`

注册自定义转换器。

```python
pw.register_transformer("reverse", lambda x: x[::-1])
```

##### `register_template(name, template)`

注册命名模板，节点中可通过名称引用。

##### `add_hook(hook)`

添加生命周期钩子函数。签名：`(event: str, node_id: str, ctx: Context) -> None`

事件类型：
- `"before_node"` — 节点执行前
- `"after_node"` — 节点执行后
- `"on_error"` — 节点出错时

#### 执行

##### `run(variables=None) → Context`

执行工作流，返回完整上下文。出错时抛异常。

##### `safe_run(variables=None) → RunResult`

安全执行，不抛异常。返回 `RunResult(success, context, error)`。

##### `dry_run(variables=None) → List[str]`

模拟执行，返回将经过的节点 ID 列表（不实际渲染模板）。

##### `validate() → Dict[str, Any]`

验证工作流配置。返回：

```python
{
    "valid": bool,
    "errors": [str],       # 引用了不存在的节点
    "warnings": [str],     # 不可达的节点等
    "unreachable": [str],  # 从 start_node 无法到达的节点
    "orphaned": [str]      # 没有被任何节点引用的节点
}
```

#### 序列化

##### `from_yaml(yaml_content) → PromptWeaver` (classmethod)

从 YAML 字符串创建工作流。

##### `to_dict() → Dict[str, Any]`

导出为字典。

##### `from_dict(data) → PromptWeaver` (classmethod)

从字典恢复。

##### `to_json(indent=2) → str`

导出 JSON 字符串。

##### `from_json(json_str) → PromptWeaver` (classmethod)

从 JSON 恢复。

##### `to_mermaid() → str`

生成 Mermaid 流程图字符串。

#### 其他

##### `merge(other, prefix="") → PromptWeaver`

合并另一个工作流的节点到当前实例，可添加前缀避免 ID 冲突。

##### `pipeline_stats() → dict`

返回工作流统计信息（节点数、类型分布等）。

---

### Chain

链式 API 构建器，提供更流畅的语法。

```python
from weaver import Chain

ctx = (Chain()
    .prompt("Hello, {{name}}!")
    .transform("upper")
    .output()
    .run({"name": "World"}))
```

#### 方法

##### `prompt(template) → Chain`

添加模板渲染步骤。

##### `condition(cond, true_template, false_template) → Chain`

添加条件步骤。`cond` 同 `PromptWeaver.add_condition` 的 condition 参数。

##### `transform(*transforms) → Chain`

添加转换步骤，接受多个转换器名称。

##### `output(key="result") → Chain`

添加输出步骤。

##### `run(variables=None) → Context`

执行链并返回上下文。

##### `to_mermaid() → str`

生成流程图。

---

### Context

执行上下文，贯穿整个工作流生命周期。

| 属性 | 类型 | 说明 |
|------|------|------|
| `variables` | `Dict[str, Any]` | 所有变量 |
| `history` | `List[Dict]` | 执行历史 `[{node, output}, ...]` |
| `current_output` | `Any` | 当前最新输出 |
| `errors` | `Dict[str, Exception]` | 按节点 ID 记录的错误 |
| `parallel_results` | `Dict[str, List[Any]]` | 并行节点的结果 |

#### 方法

##### `set(key, value)` / `get(key, default=None)`

读写变量。

##### `push_history(node_id, output)`

记录执行历史（自动调用）。

##### `snapshot() → Dict` / `restore(snap)`

保存/恢复上下文状态，用于回滚或分支。

---

## 数据类

### Node

```python
@dataclass
class Node:
    id: str
    type: NodeType
    config: Dict[str, Any]       # 节点配置（模板、条件、转换器等）
    next: Optional[str] = None   # 下一个节点
    branches: Dict[str, str]     # 分支映射
    max_retries: int = 0
    retry_delay: float = 0
    on_error: Optional[Callable] = None
```

### NodeType

```python
class NodeType(Enum):
    PROMPT = "prompt"
    CONDITION = "condition"
    TRANSFORM = "transform"
    OUTPUT = "output"
    LOOP = "loop"
    PARALLEL = "parallel"
    TRY_CATCH = "try_catch"
    SUBWORKFLOW = "subworkflow"
    MAP_REDUCE = "map_reduce"
```

### RunResult

```python
@dataclass
class RunResult:
    success: bool
    context: Optional[Context] = None
    error: Optional[Exception] = None
```

### NodeMetrics

```python
@dataclass
class NodeMetrics:
    node_id: str
    duration_ms: float = 0.0
    attempts: int = 0
    status: str = "success"  # "success" | "error" | "skipped"
```

### ExecutionMetrics

```python
@dataclass
class ExecutionMetrics:
    total_duration_ms: float = 0.0
    nodes: List[NodeMetrics] = []
    start_time: float = 0.0

    @property
    def node_count(self) -> int: ...

    @property
    def error_count(self) -> int: ...
```

通过 `pw.metrics` 访问最近一次执行的指标。

---

## 便捷函数

### `weave(template, variables=None) → str`

单步模板渲染快捷方式。

```python
from weaver import weave
weave("Hello, {{name | upper}}!", {"name": "world"})
# => "HELLO, WORLD!"
```

### `weave_chain(templates, variables=None) → str`

多模板链式渲染，前一个的输出作为后一个的输入。

```python
from weaver import weave_chain
weave_chain(["{{text}}", "{{output | upper}}"], {"text": "hello"})
# => "HELLO"
```

### `weave_file(path, variables=None) → str`

从文件加载模板并渲染。

### `weave_parallel(templates, variables=None) → List[str]`

并行渲染多个模板，返回结果列表。

### `weave_merge(templates, variables=None, separator="\n") → str`

并行渲染多个模板后合并为一个字符串。

---

## CLI

```bash
python -m weaver.cli <command> [options]
```

| 命令 | 说明 |
|------|------|
| `run <file.yaml>` | 执行 YAML 工作流 |
| `render <template>` | 快速渲染单个模板 |
| `export <file.yaml> -o out.json` | 导出为 JSON |
| `import <file.json>` | 从 JSON 导入工作流 |
| `validate <file.yaml>` | 验证工作流配置 |
| `mermaid <file.yaml>` | 输出 Mermaid 流程图 |
| `list-transformers` | 列出所有已注册转换器 |
| `demo` | 运行内置演示 |

**通用选项：**
- `--var KEY=VALUE` — 传入变量（可多次使用）
- `--verbose` — 详细输出

---

*Prompt Weaver v0.3.0 · Code Lab · 2026*
