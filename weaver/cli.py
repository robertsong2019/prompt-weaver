#!/usr/bin/env python3
"""
Prompt Weaver CLI

Usage:
    python cli.py run <workflow.yaml> [--var key=value]
    python cli.py render <template> [--var key=value]
    python cli.py export <workflow.yaml> [--output out.json]
    python cli.py import <workflow.json> [--var key=value]
    python cli.py validate <workflow.yaml>
    python cli.py list-transformers
    python cli.py mermaid <workflow.yaml>
    python cli.py demo
"""

import sys
import json
import argparse
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from weaver import PromptWeaver, Chain, weave


def parse_vars(var_list):
    """解析 --var 参数"""
    result = {}
    for item in var_list or []:
        if "=" in item:
            key, value = item.split("=", 1)
            # Try to parse JSON values (lists, numbers, bools)
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                result[key] = value
    return result


def cmd_run(args):
    """运行 YAML 工作流"""
    yaml_path = Path(args.workflow)
    if not yaml_path.exists():
        print(f"Error: File not found: {yaml_path}")
        return 1

    yaml_content = yaml_path.read_text()
    variables = parse_vars(args.var)

    try:
        weaver = PromptWeaver.from_yaml(yaml_content)
        ctx = weaver.run(variables)

        print("\n=== Result ===")
        print(ctx.current_output)

        if args.debug:
            print("\n=== History ===")
            for i, step in enumerate(ctx.history, 1):
                print(f"  {i}. {step['node']}: {repr(step['output'])[:120]}")

        if args.json:
            print("\n=== JSON Output ===")
            print(json.dumps({
                "output": ctx.current_output,
                "variables": {k: v for k, v in ctx.variables.items() if not k.startswith("_")},
                "history": [{"node": h["node"], "output": repr(h["output"])[:100]} for h in ctx.history]
            }, indent=2, ensure_ascii=False, default=str))

        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def cmd_render(args):
    """快速渲染模板"""
    variables = parse_vars(args.var)
    result = weave(args.template, variables)
    print(result)
    return 0


def cmd_export(args):
    """导出工作流为 JSON"""
    yaml_path = Path(args.workflow)
    if not yaml_path.exists():
        print(f"Error: File not found: {yaml_path}")
        return 1

    try:
        yaml_content = yaml_path.read_text()
        weaver = PromptWeaver.from_yaml(yaml_content)
        json_str = weaver.to_json()

        if args.output:
            Path(args.output).write_text(json_str)
            print(f"Exported to {args.output}")
        else:
            print(json_str)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def cmd_import(args):
    """导入 JSON 工作流并运行"""
    json_path = Path(args.workflow)
    if not json_path.exists():
        print(f"Error: File not found: {json_path}")
        return 1

    try:
        json_content = json_path.read_text()
        weaver = PromptWeaver.from_json(json_content)
        variables = parse_vars(args.var)
        ctx = weaver.run(variables)

        print("\n=== Result ===")
        print(ctx.current_output)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def cmd_validate(args):
    """验证工作流文件"""
    path = Path(args.workflow)
    if not path.exists():
        print(f"Error: File not found: {path}")
        return 1

    try:
        content = path.read_text()
        if path.suffix in ('.json',):
            weaver = PromptWeaver.from_json(content)
        else:
            weaver = PromptWeaver.from_yaml(content)

        errors = []

        # Check start node
        if not weaver.start_node:
            errors.append("No start node defined")
        elif weaver.start_node not in weaver.nodes:
            errors.append(f"Start node '{weaver.start_node}' not found in nodes")

        # Check node references
        for nid, node in weaver.nodes.items():
            if node.next and node.next not in weaver.nodes:
                errors.append(f"Node '{nid}' references missing next node '{node.next}'")
            for branch_name, target in node.branches.items():
                if target not in weaver.nodes:
                    errors.append(f"Node '{nid}' branch '{branch_name}' references missing node '{target}'")

        # Check for unreachable nodes
        reachable = set()
        if weaver.start_node:
            queue = [weaver.start_node]
            while queue:
                current = queue.pop(0)
                if current in reachable or current not in weaver.nodes:
                    continue
                reachable.add(current)
                node = weaver.nodes[current]
                if node.next:
                    queue.append(node.next)
                for target in node.branches.values():
                    queue.append(target)

        unreachable = set(weaver.nodes.keys()) - reachable
        if unreachable:
            errors.append(f"Unreachable nodes: {', '.join(sorted(unreachable))}")

        if errors:
            print("❌ Validation failed:")
            for e in errors:
                print(f"  - {e}")
            return 1

        print(f"✅ Valid workflow: {len(weaver.nodes)} nodes, start={weaver.start_node}")
        print(f"   Nodes: {', '.join(weaver.nodes.keys())}")
        return 0
    except Exception as e:
        print(f"❌ Parse error: {e}")
        return 1


def cmd_list_transformers(args):
    """列出所有内置转换器"""
    weaver = PromptWeaver()
    print("Built-in Transformers:")
    print("=" * 40)
    for name, func in sorted(weaver.transformers.items()):
        # Get a short description from docstring or function name
        doc = (func.__doc__ or "").strip().split("\n")[0] if func.__doc__ else ""
        desc = f" - {doc}" if doc else ""
        print(f"  {name}{desc}")
    print(f"\nTotal: {len(weaver.transformers)} transformers")
    return 0


def cmd_mermaid(args):
    """生成 Mermaid 流程图"""
    path = Path(args.workflow)
    if not path.exists():
        print(f"Error: File not found: {path}")
        return 1

    try:
        content = path.read_text()
        if path.suffix in ('.json',):
            weaver = PromptWeaver.from_json(content)
        else:
            weaver = PromptWeaver.from_yaml(content)
        print(weaver.to_mermaid())
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def cmd_demo(args):
    """运行演示"""
    print("=" * 50)
    print("Prompt Weaver Demo")
    print("=" * 50)

    # Demo 1: 简单模板
    print("\n1. 简单模板渲染")
    print("-" * 30)
    result = weave("Hello, {{name}}! Today is {{day}}.", {
        "name": "World",
        "day": "Thursday"
    })
    print(f"结果: {result}")

    # Demo 2: 链式调用
    print("\n2. 链式调用")
    print("-" * 30)
    chain = (Chain()
        .prompt("User: {{username}}")
        .transform("upper")
        .output())
    ctx = chain.run({"username": "alice"})
    print(f"结果: {ctx.current_output}")

    # Demo 3: 条件分支
    print("\n3. 条件分支")
    print("-" * 30)
    weaver = PromptWeaver()
    weaver.add_prompt("start", "Score: {{score}}", next_node="check")
    weaver.add_condition("check", lambda ctx: ctx.get("score") >= 60, "pass", "fail")
    weaver.add_prompt("pass", "🎉 Congratulations! You passed!")
    weaver.add_prompt("fail", "😢 Better luck next time!")

    for score in [85, 45]:
        ctx = weaver.run({"score": score})
        print(f"Score {score}: {ctx.current_output}")

    # Demo 4: 带过滤器的模板
    print("\n4. 带过滤器的模板")
    print("-" * 30)
    result = weave("Name: {{name | upper}}, Length: {{text | length}}", {
        "name": "alice",
        "text": "Hello World"
    })
    print(f"结果: {result}")

    # Demo 5: 复杂工作流
    print("\n5. 复杂工作流")
    print("-" * 30)
    weaver = PromptWeaver()
    weaver.add_prompt("greet", "Hello, {{name | upper}}!")
    weaver.add_transform("split", ["split"])
    weaver.add_transform("count", ["length"])
    weaver.add_output("result")
    weaver.nodes["greet"].next = "split"
    weaver.nodes["split"].next = "count"
    weaver.nodes["count"].next = "result"

    ctx = weaver.run({"name": "OpenClaw Agent"})
    print(f"单词数: {ctx.current_output}")

    # Demo 6: 生成 Mermaid 图
    print("\n6. 生成 Mermaid 流程图")
    print("-" * 30)
    weaver = PromptWeaver()
    weaver.add_prompt("input", "Process: {{data}}")
    weaver.add_condition("check", lambda ctx: len(ctx.get("data", "")) > 5, "long", "short")
    weaver.add_prompt("long", "Long data detected")
    weaver.add_prompt("short", "Short data detected")

    print(weaver.to_mermaid())

    # Demo 7: 导出为 JSON
    print("\n7. 导出为 JSON")
    print("-" * 30)
    weaver = PromptWeaver()
    weaver.add_prompt("start", "Hello, {{name}}!", next_node="upper")
    weaver.add_transform("upper", ["upper"], next_node="end")
    weaver.add_output("end")
    print(weaver.to_json())

    print("\n" + "=" * 50)
    print("Demo completed!")
    print("=" * 50)

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Prompt Weaver - 轻量级 Prompt 编排引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s render "Hello, {{name}}!" --var name=World
  %(prog)s run workflow.yaml --var name=World --debug
  %(prog)s export workflow.yaml --output workflow.json
  %(prog)s import workflow.json --var name=World
  %(prog)s validate workflow.yaml
  %(prog)s list-transformers
  %(prog)s mermaid workflow.yaml
  %(prog)s demo
""")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # run 命令
    run_parser = subparsers.add_parser("run", help="运行 YAML 工作流")
    run_parser.add_argument("workflow", help="YAML 工作流文件")
    run_parser.add_argument("--var", "-v", action="append", help="变量 (key=value, JSON supported)")
    run_parser.add_argument("--debug", "-d", action="store_true", help="显示执行历史")
    run_parser.add_argument("--json", "-j", action="store_true", help="输出 JSON 格式结果")

    # render 命令
    render_parser = subparsers.add_parser("render", help="快速渲染模板")
    render_parser.add_argument("template", help="模板字符串")
    render_parser.add_argument("--var", "-v", action="append", help="变量 (key=value, JSON supported)")

    # export 命令
    export_parser = subparsers.add_parser("export", help="导出工作流为 JSON")
    export_parser.add_argument("workflow", help="YAML 工作流文件")
    export_parser.add_argument("--output", "-o", help="输出文件路径 (默认 stdout)")

    # import 命令
    import_parser = subparsers.add_parser("import", help="导入 JSON 工作流并运行")
    import_parser.add_argument("workflow", help="JSON 工作流文件")
    import_parser.add_argument("--var", "-v", action="append", help="变量 (key=value)")

    # validate 命令
    validate_parser = subparsers.add_parser("validate", help="验证工作流文件")
    validate_parser.add_argument("workflow", help="YAML 或 JSON 工作流文件")

    # list-transformers 命令
    subparsers.add_parser("list-transformers", help="列出所有内置转换器")

    # mermaid 命令
    mermaid_parser = subparsers.add_parser("mermaid", help="生成 Mermaid 流程图")
    mermaid_parser.add_argument("workflow", help="YAML 或 JSON 工作流文件")

    # demo 命令
    subparsers.add_parser("demo", help="运行演示")

    args = parser.parse_args()

    handlers = {
        "run": cmd_run,
        "render": cmd_render,
        "export": cmd_export,
        "import": cmd_import,
        "validate": cmd_validate,
        "list-transformers": cmd_list_transformers,
        "mermaid": cmd_mermaid,
        "demo": cmd_demo,
    }

    handler = handlers.get(args.command)
    if handler:
        return handler(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
