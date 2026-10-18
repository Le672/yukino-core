"""Command-only example plugin. No eval, network access or package installation."""
import ast
import operator
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}


def handle(text, context):
    expression = text.partition(" ")[2].strip()
    if not expression: return "用法：/计算 (12+3)*4/2"
    if len(expression) > 160: return "算式太长啦"
    try:
        tree = ast.parse(expression, mode="eval")
        if len(list(ast.walk(tree))) > 60: return "算式太复杂了"
        def visit(node):
            if isinstance(node, ast.Expression): return visit(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float) and abs(node.value) < 1e20: return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in OPS: return OPS[type(node.op)](visit(node.left), visit(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in OPS: return OPS[type(node.op)](visit(node.operand))
            raise ValueError()
        result = visit(tree)
        return f"{expression} = {result:.12g}"
    except Exception: return "这个式子算不了。支持数字、括号和 + - * / %"
