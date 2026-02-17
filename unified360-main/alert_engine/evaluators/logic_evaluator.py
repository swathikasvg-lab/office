# alert_engine/evaluators/logic_evaluator.py

def compare(a, op, b):
    # If either side is None, condition is false except !=
    if a is None:
        if op in ["!=", "!="]:
            return True if b is not None else False
        return False

    # String operations
    if op in ["=", "=="]:
        return str(a) == str(b)
    if op == "!=":
        return str(a) != str(b)

    # Numeric operations
    try:
        a = float(a)
        b = float(b)
    except Exception:
        return False

    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b

    return False


def evaluate_node(node, metrics):
    # condition
    if "field" in node:
        f = node["field"]
        op = node["op"]
        v = node["value"]
        return compare(metrics.get(f), op, v)

    # group
    if node["op"] == "AND":
        return all(evaluate_node(c, metrics) for c in node["children"])
    if node["op"] == "OR":
        return any(evaluate_node(c, metrics) for c in node["children"])

    return False

