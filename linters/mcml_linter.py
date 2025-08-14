from lark import Token
from copy import deepcopy
import json
import sys
from common import DSLException, DSLWarning, check_metavars_placeholders, check_subterms, check_universal_rule, lint_code

class MCmlException(DSLException):
    """Custom exception for MCml"""
    def __init__(self, message, item=None):
        super().__init__(message, item)

class MCmlWarning(DSLWarning):

    def __init__(self, message, item=None, code=""):
        super().__init__(message, item, code)

def check_semantics(tree):
    """
    Checks if there 1 mc value, as well as 1 static and 1 runtime patterns
    Checks if pattern names are unique
    Checks if only defined patterns are used and args are passed correctly
    Checks usage of static metavars in runtime and if special runtime placeholders are not overriden

    + checks from csml (metavar - placeholder correspondence; subterm checks)
    """

    # check if there 1 mc value, as well as 1 static and 1 runtime patterns
    def check_patterns_mc(tree):

        def first_line_meta(node):
            """Return a copy of node.meta that only spans the first line."""
            meta = deepcopy(node.meta)

            # Find first and last tokens *on the first line only*
            def tokens_on_first_line(t):
                if isinstance(t, Token):
                    if 'NEWLINE' in t.type:
                        return
                    if t.line == meta.line:  # only tokens on first line
                        yield t
                else:
                    for c in t.children:
                        yield from tokens_on_first_line(c)

            toks = list(tokens_on_first_line(node))
            if toks:
                meta.end_column = toks[-1].end_column  # exclusive end
            return meta

        mc = list(tree.find_data("mc"))
        if len(mc) != 1:
            second_obj = mc[1] if len(mc) > 1 else None
            raise MCmlException("There must be exactly one mc value defined in the policy.", first_line_meta(second_obj) if second_obj else None)
        static = list(tree.find_data("pattern_static"))
        if len(static) != 1:
            second_obj = static[1] if len(static) > 1 else None
            raise MCmlException("There must be exactly one static pattern defined in the policy.", first_line_meta(second_obj) if second_obj else None)
        runtime = list(tree.find_data("pattern_runtime"))
        if len(runtime) != 1:
            second_obj = runtime[1] if len(runtime) > 1 else None
            raise MCmlException("There must be exactly one runtime pattern defined in the policy.", first_line_meta(second_obj) if second_obj else None)
        
    def check_for_universal_rules(tree):
        warnings = []

        static_pattern = next(tree.find_data("pattern_static"))
        warnings += check_universal_rule(MCmlWarning, static_pattern, "static_rule", ['loc', 'ins', 'expr', 'look_ahead'])
        runtime_pattern = next(tree.find_data("pattern_runtime"))
        warnings += check_universal_rule(MCmlWarning, runtime_pattern, "runtime_rule", ['name_op', 'compare_contents', 'func'])

        aux_patterns = list(tree.find_data("pattern"))
        for pattern in aux_patterns:
            fields = ["loc", "ins_placeholders", "func"]
            rule_token = "pattern_expr_rule" if any(pattern.find_data("pattern_expr_rule")) else "pattern_rule"
            if rule_token == "pattern_expr_rule":
                fields.insert(2, "expr")
            warnings += check_universal_rule(MCmlWarning, pattern, rule_token, fields)

        return warnings
    
    # check if pattern names are unique and all are called
    def check_unique_pattern_names(tree):
        patterns = {} # name:(arg_count, token)
        pattern_signatures = list(tree.find_data("pattern_signature"))
        for signature in pattern_signatures:
            name_token = next(signature.scan_values(lambda t: "IDENTIFIER" in t.type))
            if name_token.value in patterns:
                raise MCmlException(f"Pattern name '{name_token.value}' is not unique.", name_token)
            else:
                arg_count = 0 if len(signature.children) == 1 else len(signature.children[-1].children)
                patterns[name_token.value] = (arg_count, name_token)

        pattern_calls = list(tree.find_data("pattern_call"))
        called_pattern_names = set()
        for call in pattern_calls:
            name_token = next(call.scan_values(lambda t: "IDENTIFIER" in t.type))
            called_pattern_names.add(name_token.value)
            if name_token.value not in patterns:
                raise MCmlException(f"Pattern '{name_token.value}' is used in the static pattern but not defined in the policy.", name_token)
            else:
                arg_count = 0 if len(call.children) == 1 else len(call.children[-1].children)
                if arg_count != patterns[name_token.value][0]:
                    raise MCmlException(f"Pattern '{name_token.value}' expects {patterns[name_token.value][0]} args, but {len(call.children) - 1} were provided.", name_token)

        warnings = []
        for name in set(patterns.keys()) - called_pattern_names:
            warnings.append(MCmlWarning(f"Pattern '{name}' is defined but never called.", patterns[name][1]))
        
        return warnings
    
    # warns if args passed to patterns are unused or redefined
    def check_pattern_args_usage(tree):
        warnings = []

        patterns = list(tree.find_data("pattern"))
        for pattern in patterns:
            received_metavars = {}
            args = next(pattern.find_data("pattern_args"), None)
            if args:
                for arg in args.children:
                    received_metavars[arg.children[0].value[1:]] = arg.children[0]
                rules = next(pattern.find_data("pattern_rules"), None)
                for metavar in rules.scan_values(lambda t: 'NAMED_METAVAR' in t.type):
                    if metavar.value[1:] in received_metavars:
                        warnings.append(MCmlWarning(f"Metavar ?{metavar.value[1:]} is received as an argument and is being redefined.", received_metavars[metavar.value[1:]]))
                for placeholder in rules.scan_values(lambda t: 'NAMED_PLACEHOLDER' in t.type):
                    if placeholder.value[1:] in received_metavars:
                        del received_metavars[placeholder.value[1:]]
                for unused_arg in received_metavars:
                    warnings.append(MCmlWarning(f"Argument '?{unused_arg}' is defined but never used in the pattern.", received_metavars[unused_arg]))

        return warnings

    # check usage of metavars from the static pattern in runtime and if special runtime placeholders are not overriden in the static pattern
    def check_static_metavars_runtime(tree):

        saved = {}
        save_in_instances = list(tree.find_data("save_in"))
        for save_in in save_in_instances:
            metavar = next(save_in.scan_values(lambda t: 'NAMED_METAVAR' in t.type), None)
            if metavar.value in ["?n", "?c1", "?c2"]:
                raise MCmlException(f"Static pattern cannot save metavariable '{metavar.value}' as it conflicts with the reserved names for the runtime pattern ('n', 'c1', 'c2').", metavar)
            else:
                saved[metavar.value[1:]] = metavar

        is_in_instances = list(tree.find_data("is_in"))
        used = set()
        for is_in in is_in_instances:
            named_place = next(is_in.scan_values(lambda t: 'NAMED_PLACEHOLDER' in t.type), None)
            used.add(named_place.value[1:])
            if named_place.value[1:] not in saved:
                raise MCmlException(f"Runtime pattern uses static metavariable '{named_place.value}' that is not saved in the static pattern.", named_place)
            
        warnings = []
        for name in set(saved.keys()) - used:
            warnings.append(MCmlWarning(f"Static pattern saves metavariable '?{name}' but it is never used in the runtime pattern.", saved[name]))
        
        return warnings

    # checks from csml
    def check_metavars_placeholders_static_runtime(tree):
        warnings = []

        static_pattern = next(tree.find_data("pattern_static"))
        static_fields = ['ins', 'expr', 'look_ahead']
        warnings += check_metavars_placeholders(MCmlException, MCmlWarning, static_pattern, "static_rule", static_fields)
            
        saved = {}
        save_in_instances = list(tree.find_data("save_in"))
        for save_in in save_in_instances:
            metavar = next(save_in.scan_values(lambda t: 'NAMED_METAVAR' in t.type), None)
            saved[metavar.value[1:]] = None

        runtime_pattern = next(tree.find_data("pattern_runtime"))
        runtime_fields = ["func"] # name has already been checked, and compare is limited by the grammar
        starter_metavars = {"n": None, "c1": None, "c2": None}
        starter_metavars.update(saved)
        warnings += check_metavars_placeholders(MCmlException, MCmlWarning, runtime_pattern, "runtime_rule", runtime_fields, starter_metavars, True)
        
        return warnings

    def check_metavars_placeholders_subterms_aux_patterns(tree):
        warnings = []

        for pattern in tree.find_data("pattern"):
            received_metavars = {}
            args = next(pattern.find_data("pattern_args"), None)
            if args:
                for arg in args.children:
                    received_metavars[arg.children[0].value[1:]] = arg.children[0]
            fields = ["loc", "ins_placeholders", "func"]
            rule_token = "pattern_expr_rule" if any(pattern.find_data("pattern_expr_rule")) else "pattern_rule"
            if rule_token == "pattern_expr_rule":
                fields.insert(2, "expr")
            warnings += check_metavars_placeholders(MCmlException, MCmlWarning, pattern, rule_token, fields, received_metavars)

            if rule_token == "pattern_expr_rule":
                check_subterms(MCmlException, pattern)

        return warnings

    check_patterns_mc(tree)
    warnings = check_for_universal_rules(tree)
    warnings += check_unique_pattern_names(tree)
    warnings += check_pattern_args_usage(tree)
    warnings += check_static_metavars_runtime(tree)
    warnings += check_metavars_placeholders_static_runtime(tree)
    warnings += check_metavars_placeholders_subterms_aux_patterns(tree)

    return warnings

if __name__ == "__main__":
    while True:
        line = sys.stdin.readline()
        if not line:
            break  # EOF
        try:
            data = json.loads(line)
            code = data.get("code", "")
            issues = lint_code(code, "mcml.lark", check_semantics)
            print(json.dumps(issues))
            sys.stdout.flush()
        except Exception as e:
            # You can print to stderr for debugging
            sys.stderr.write(f"Error: {str(e)}\n")
            sys.stderr.flush()
            print(json.dumps([]))
            sys.stdout.flush()