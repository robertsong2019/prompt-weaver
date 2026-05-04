# Prompt Weaver 教程 🧵

> 从零开始，掌握轻量级 Prompt 编排引擎

## 目录

1. [Hello Weaver — 5 分钟上手](#1-hello-weaver--5-分钟上手)
2. [链式 API — 流畅构建](#2-链式-api--流畅构建)
3. [条件与分支](#3-条件与分支)
4. [自定义转换器](#4-自定义转换器)
5. [循环与迭代](#5-循环与迭代)
6. [错误处理与重试](#6-错误处理与重试)
7. [并行执行](#7-并行执行)
8. [子工作流与模块化](#8-子工作流与模块化)
9. [Map-Reduce 批量处理](#9-map-reduce-批量处理)
10. [迭代优化（Refine）](#10-迭代优化refine)
11. [YAML 声明式工作流](#11-yaml-声明式工作流)
12. [验证、调试与可视化](#12-验证调试与可视化)
13. [序列化与分享](#13-序列化与分享)
14. [实战案例：AI Agent 任务路由](#14-实战案例ai-agent-任务路由)

---

## 1. Hello Weaver — 5 分钟上手

最简单的用法：一行代码渲染模板。

```python
from weaver import weave

result = weave("Hello, {{name}}!", {"name": "World"})
print(result)  # => Hello, World!
```

**模板语法：**
- `{{variable}}` — 插入变量
- `{{variable | filter}}` — 变量 + 过滤器
- `{{variable | filter1 | filter2}}` — 管道式多过滤器

```python
weave("{{text | upper | trim}}", {"text": "  hello  "})
# => "HELLO"
```

### 安装

```bash
# 零依赖！只需要 Python 3.9+
pip install -e .  # 或者直接把 weaver/ 目录放到你的项目中
```

---

## 2. 链式 API — 流畅构建

当单步渲染不够时，用 `Chain` 把多个步骤串起来：

```python
from weaver import Chain

ctx = (Chain()
    .prompt("My name is {{name}}")
    .transform("upper")
    .output()
    .run({"name": "Alice"}))

print(ctx.current_output)  # => "MY NAME IS ALICE"
```

**数据流：** 前一步的输出自动成为下一步的输入。

多过滤器管道：

```python
ctx = (Chain()
    .prompt("{{text}}")
    .transform("trim", "lower", "split")  # 依次应用
    .output()
    .run({"text": "  ONE TWO THREE  "}))

print(ctx.current_output)  # => ['one', 'two', 'three']
```

---

## 3. 条件与分支

### 用函数判断

```python
from weaver import Chain

ctx = (Chain()
    .prompt("Score: {{score}}")
    .condition(
        lambda ctx: ctx.get("score") >= 60,
        "🎉 Passed!",
        "📚 Try again"
    )
    .output()
    .run({"score": 85}))

print(ctx.current_output)  # => "🎉 Passed!"
```

### 用字符串表达式

```python
from weaver import PromptWeaver

pw = PromptWeaver()
pw.add_prompt("start", "Evaluating {{name}}...", next_node="check")
pw.add_condition("check", "{{score}} >= 90", "excellent", "good")
pw.add_prompt("excellent", "🏆 Excellent!")
pw.add_prompt("good", "👍 Good job!")
pw.start_node = "start"

ctx = pw.run({"name": "Alice", "score": 95})
print(ctx.current_output)  # => "🏆 Excellent!"
```

### 多级条件

```python
pw = PromptWeaver()
pw.add_prompt("start", "Score: {{score}}", next_node="grade_a")
pw.add_condition("grade_a", "{{score}} >= 90", "a", "grade_b")
pw.add_condition("grade_b", "{{score}} >= 80", "b", "grade_c")
pw.add_condition("grade_c", "{{score}} >= 70", "c", "f")
pw.add_prompt("a", "A - Excellent")
pw.add_prompt("b", "B - Good")
pw.add_prompt("c", "C - Average")
pw.add_prompt("f", "F - Failed")
pw.start_node = "start"
```

---

## 4. 自定义转换器

内置过滤器不够？自己写：

```python
from weaver import PromptWeaver

pw = PromptWeaver()

# 注册转换器
pw.register_transformer("reverse", lambda x: x[::-1])
pw.register_transformer("word_count", lambda x: len(str(x).split()))
pw.register_transformer("title_case", lambda x: str(x).title())

# 使用
pw.add_prompt("start", "{{text}}", next_node="process")
pw.add_transform("process", ["trim", "title_case"], next_node="output")
pw.add_output("output")
pw.start_node = "start"

ctx = pw.run({"text": "  hello world from weaver  "})
print(ctx.current_output)  # => "Hello World From Weaver"
```

---

## 5. 循环与迭代

### 计数循环

```python
from weaver import PromptWeaver

pw = PromptWeaver()
pw.add_prompt("start", "Starting countdown", next_node="loop")
pw.add_loop("loop", "count", {"count": 3}, next_node="output")
pw.add_output("output")
pw.start_node = "start"
```

### For 循环（遍历列表）

```python
pw = PromptWeaver()
pw.add_prompt("start", "Processing items", next_node="loop")
pw.add_loop("loop", "for", {
    "items": "tasks",  # 上下文中的变量名
    "var": "task"      # 循环变量名
}, next_node="output")
pw.add_prompt("output", "Done!")
pw.start_node = "start"

ctx = pw.run({"tasks": ["write tests", "fix bugs", "deploy"]})
```

### While 循环（条件循环）

```python
pw.add_loop("loop", "while", {
    "condition": lambda ctx: ctx.get("counter", 0) < 5
}, next_node="output")
```

---

## 6. 错误处理与重试

### Try-Catch

```python
pw = PromptWeaver()
pw.add_prompt("start", "Risky operation", next_node="safe_exec")
pw.add_try_catch("safe_exec", try_node="risky", catch_node="fallback")
pw.add_prompt("risky", "{{undefined_var}}")  # 可能失败
pw.add_prompt("fallback", "Using default value")
pw.add_output("result")
pw.start_node = "start"

result = pw.safe_run({"name": "test"})
```

### 自动重试

```python
pw.add_prompt("flaky_api", "Calling API...", 
              max_retries=3, retry_delay=0.5)
```

### Safe Run

```python
result = pw.safe_run({"input": "test"})
if result.success:
    print(result.context.current_output)
else:
    print(f"Failed: {result.error}")
```

---

## 7. 并行执行

多个分支同时执行，结果合并：

```python
from weaver import PromptWeaver

pw = PromptWeaver()
pw.add_prompt("start", "Analyzing {{text}}", next_node="parallel")
pw.add_parallel("parallel", {
    "sentiment": "sentiment_node",
    "keywords": "keyword_node",
    "summary": "summary_node"
}, next_node="combine")

pw.add_prompt("sentiment_node", "Sentiment: positive")
pw.add_prompt("keyword_node", "Keywords: AI, agent")
pw.add_prompt("summary_node", "Summary: about AI agents")

pw.add_prompt("combine", "Analysis complete", next_node="output")
pw.add_output("output")
pw.start_node = "start"

ctx = pw.run({"text": "AI agents are transforming software"})
# ctx.parallel_results["parallel"] 包含三个分支的结果
```

---

## 8. 子工作流与模块化

将可复用逻辑封装为子工作流：

```python
from weaver import PromptWeaver

# 可复用的翻译模块
translator = PromptWeaver()
translator.add_prompt("t", "{{input | upper}}")  # 简化为大写
translator.add_output("t_out")
translator.start_node = "t"

# 主工作流
main = PromptWeaver()
main.add_prompt("start", "Processing: {{message}}", next_node="translate")
main.add_subworkflow("translate", translator,
                     input_mapping={"input": "message"},
                     output_key="translated",
                     next_node="output")
main.add_output("output")
main.start_node = "start"

ctx = main.run({"message": "hello"})
```

### Merge — 合并工作流

```python
# 把另一个工作流的所有节点合并进来
main.merge(translator, prefix="tr_")
# translator 的节点现在以 "tr_" 前缀存在于 main 中
```

---

## 9. Map-Reduce 批量处理

批量处理列表数据：

```python
from weaver import PromptWeaver

pw = PromptWeaver()
pw.add_map_reduce(
    "greet_all",
    items_expr="names",        # 上下文变量名（列表）
    item_var="name",           # 循环变量
    map_template="Hello, {{name}}!",  # 每个元素的模板
    reduce_strategy="join",    # 合并策略
    next_node="output"
)
pw.add_output("output")
pw.start_node = "greet_all"

ctx = pw.run({"names": ["Alice", "Bob", "Charlie"]})
print(ctx.current_output)
# => "Hello, Alice!\nHello, Bob!\nHello, Charlie!"
```

**Reduce 策略：**

| 策略 | 效果 |
|------|------|
| `"join"` | 用换行符连接 |
| `"concat"` | 直接拼接 |
| `"sum"` | 数值求和 |
| `"first"` | 取第一个 |
| `"last"` | 取最后一个 |
| 自定义函数 | `lambda items: ", ".join(items)` |

---

## 10. 迭代优化（Refine）

反复执行直到收敛：

```python
from weaver import PromptWeaver

pw = PromptWeaver()
pw.add_prompt("start", "Improve: {{draft}}", next_node="refine")
pw.add_refine("refine",
              template="Refined version {{output}}",
              max_rounds=5,
              convergence_fn=lambda prev, curr: prev == curr,
              next_node="output")
pw.add_output("output")
pw.start_node = "start"
```

适用场景：文本优化、答案精炼、渐进式代码生成。

---

## 11. YAML 声明式工作流

用 YAML 定义工作流，更易读和维护：

```yaml
# scoring.yaml
- id: input
  type: prompt
  template: "Student: {{name}}, Score: {{score}}"
  next: grade_check

- id: grade_check
  type: condition
  condition: "{{score}} >= 90"
  true: excellent
  false: check_pass

- id: check_pass
  type: condition
  condition: "{{score}} >= 60"
  true: pass
  false: fail

- id: excellent
  type: prompt
  template: "🏆 {{name}}: Excellent!"
  next: output

- id: pass
  type: prompt
  template: "👍 {{name}}: Passed!"
  next: output

- id: fail
  type: prompt
  template: "📚 {{name}}: Needs improvement"
  next: output

- id: output
  type: output
  key: result
```

运行：

```bash
python -m weaver.cli run scoring.yaml --var name=Alice --var score=95
```

Python 中加载：

```python
from weaver import PromptWeaver

with open("scoring.yaml") as f:
    pw = PromptWeaver.from_yaml(f.read())

ctx = pw.run({"name": "Alice", "score": 95})
```

---

## 12. 验证、调试与可视化

### 验证工作流

```python
result = pw.validate()
if not result["valid"]:
    print("Errors:", result["errors"])
    print("Unreachable nodes:", result["unreachable"])
```

### Dry Run（模拟执行）

```python
path = pw.dry_run({"score": 85})
print("Execution path:", path)
# => ["start", "grade_check", "check_pass", "pass", "output"]
```

### 可视化

```python
print(pw.to_mermaid())
```

```bash
python -m weaver.cli mermaid workflow.yaml
```

### 生命周期钩子

```python
def logger(event, node_id, ctx):
    if event == "before_node":
        print(f"→ Entering {node_id}")
    elif event == "after_node":
        print(f"✓ Done {node_id}: {ctx.current_output}")
    elif event == "on_error":
        print(f"✗ Error in {node_id}")

pw.add_hook(logger)
```

### 执行指标

```python
ctx = pw.run({"input": "test"})
metrics = pw.metrics
print(f"Total: {metrics.total_duration_ms:.1f}ms")
for node_metric in metrics.nodes:
    print(f"  {node_metric.node_id}: {node_metric.duration_ms:.1f}ms ({node_metric.status})")
```

---

## 13. 序列化与分享

### JSON 导出/导入

```python
# 导出
json_str = pw.to_json()

# 导入
pw2 = PromptWeaver.from_json(json_str)

# 文件操作
with open("workflow.json", "w") as f:
    f.write(pw.to_json())
```

### CLI 导出

```bash
python -m weaver.cli export workflow.yaml -o workflow.json
python -m weaver.cli import workflow.json --var name=World
```

---

## 14. 实战案例：AI Agent 任务路由

一个完整的 Agent 任务分类和路由系统：

```yaml
# agent-router.yaml
- id: receive
  type: prompt
  template: |
    User input: {{user_input}}
    Context: {{context | default("general")}}
  next: classify

- id: classify
  type: condition
  condition: "{{user_input | lower}}"
  true: check_create
  false: query_handler

- id: check_create
  type: condition
  condition: "create in {{user_input | lower}}"
  true: create_handler
  false: check_update

- id: check_update
  type: condition
  condition: "update in {{user_input | lower}}"
  true: update_handler
  false: delete_handler

- id: create_handler
  type: prompt
  template: "📝 Creating: {{user_input}}"
  next: output

- id: update_handler
  type: prompt
  template: "✏️ Updating: {{user_input}}"
  next: output

- id: delete_handler
  type: prompt
  template: "🗑️ Deleting: {{user_input}}"
  next: output

- id: query_handler
  type: prompt
  template: "🔍 Searching: {{user_input}}"
  next: output

- id: output
  type: output
  key: action
```

验证并运行：

```bash
# 验证
python -m weaver.cli validate agent-router.yaml

# 模拟执行
python -m weaver.cli run agent-router.yaml --var "user_input=create new project" --var "context=dev"

# 查看流程图
python -m weaver.cli mermaid agent-router.yaml
```

---

## 下一步

- 📖 阅读 [API 参考](API.md) 了解完整接口
- 🧪 查看 `examples/` 目录的更多示例
- 🔧 运行 `python -m weaver.cli demo` 查看内置演示

---

*Prompt Weaver · Code Lab · 2026*
