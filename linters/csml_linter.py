import sys
import json
from lark import Lark, UnexpectedInput

csml = r"""
    policy: rules default | default
    rules: rule | rule rules
    rule: loc "::" ins "::" expr "::" mem "->" dec NEWLINE
    default: "default" "->" dec NEWLINE?
    dec: concretize | simbolize | propagate | concretize_restrict | simbolize_restrict | propagate_restrict
        concretize: "C"
        simbolize: "S"
        propagate: "P"
    concretize_restrict: concretize min | concretize max | concretize avg
        min: "[min]"
        max: "[max]"
        avg: "[avg]"
    simbolize_restrict: simbolize "[" exp_placeholders "]" | simbolize "[" exp_placeholders ".." exp_placeholders "]"
    propagate_restrict: propagate p_restriction
    p_restriction: "[" exp_placeholders "]"
                | "[" exp_placeholders ".." exp_placeholders "]"
                | "[" num ";" exp_placeholders "]"
                | "[" num ";" exp_placeholders ".." exp_placeholders "]"
                | "[" num "]"
"""

#########################################################

locations = r"""
    loc: num | "[" num ".." num  "]" | wildcard
"""

# instructions correspond to BIL statements and can have metavars
instructions = r"""
    ins: "<" stmt_metavars ">" | wildcard
"""

# expressions are the building blocks of instructions and can have new metavars or placeholders (which replace the previous metavars)
expressions = r"""
    expr: "<" exp_metavars_placeholders ">" | subterm | strict_subterm | wildcard
    subterm: subterm "-<" exp_metavars_placeholders | strict_subterm "-<" exp_metavars_placeholders | exp_metavars_placeholders "-<" exp_metavars_placeholders
    strict_subterm: strict_subterm "=<" exp_metavars_placeholders | subterm "=<" exp_metavars_placeholders | exp_metavars_placeholders "=<" exp_metavars_placeholders
"""  # we allow mixed chains of subterms and strict subterms which can contain both metavars (both types) and placeholders (both types)

# memory functions can have placeholders (which replace the previous metavars)
memory = r"""
    mem: memfunc "(" exp_placeholders ")" | wildcard
    memfunc: /[a-zA-Z_][a-zA-Z0-9_]*/
"""

predicates = rf"""
    {locations}
    {instructions}
    {expressions}
    {memory}
"""

#########################################################

wildcards_placeholders_metavars = r"""
    wildcard: "*"
    metavar: /[?][a-zA-Z]+/ | anonymous // when matched become available to ALL subsequent terms through placeholders
    anonymous: "??"
    placeholder: /[!][a-zA-Z]+/ | distinguished
    distinguished: "!!"
"""

######################################################### CSml receives formal BIL as input

bil_preface = r"""
    num_metavar: num | metavar
    num_metavar_placeholder: num | metavar | placeholder
    num_placeholder: num | placeholder
    num: hex | decimal
        hex: /0x[0-9a-fA-F]+/
        decimal: /\d+/

    string_metavar: string | metavar
    string_metavar_placeholder: string | metavar | placeholder
    string_placeholder: string | placeholder
    string: "\"" /[^"]+/ "\""
"""

# for location predicates
bil_with_metavars = r"""
    bil_metavars: stmt_metavars ";" | stmt_metavars ";" bil_metavars | metavar
    
    stmt_metavars: assign | jump | cpuexn | special | while_ | if_ | if_else | metavar

    assign: var_metavars ":=" exp_metavars | metavar ":=" exp_metavars
    jump: "jmp" exp_metavars
    cpuexn: "cpuexn" "(" num_metavar ")"
    special: "special" "(" string_metavar ")"
    while_: "while" "(" exp_metavars ")" "{" bil_metavars "}"
    if_: "if" "(" exp_metavars ")" "{" bil_metavars "}"
    if_else: "if" "(" exp_metavars ")" "{" bil_metavars "}" "else" "{" bil_metavars "}"
"""

bil_expressions = r""" // we have to use val instead of word and unknown because of lark collisions
    exp_metavars: metavar | "(" exp_metavars ")" | var_metavars | val_metavars | load_metavars | store_metavars | exp_metavars bop exp_metavars | uop exp_metavars | cast_metavars | let_metavars | ite_metavars | extract_metavars | concat_metavars
    exp_metavars_placeholders: metavar | placeholder | "(" exp_metavars_placeholders ")" | var_metavars_placeholders | val_metavars_placeholders | load_metavars_placeholders | store_metavars_placeholders | exp_metavars_placeholders bop exp_metavars_placeholders | uop exp_metavars_placeholders | cast_metavars_placeholders | let_metavars_placeholders | ite_metavars_placeholders | extract_metavars_placeholders | concat_metavars_placeholders
    exp_placeholders: placeholder| "(" exp_placeholders ")" | var_placeholders | val_placeholders | load_placeholders | store_placeholders | exp_placeholders bop exp_placeholders | uop exp_placeholders | cast_placeholders | let_placeholders | ite_placeholders | extract_placeholders | concat_placeholders
    
    // mem_val represents the memory, our "mem" variable, only used internally in bil
    // mem_val: val "[" word "<-" val ":" num "]" 

    load_metavars: exp_metavars "[" exp_metavars "," endian_metavar "]" ":" num_metavar
    load_metavars_placeholders: exp_metavars_placeholders "[" exp_metavars_placeholders "," endian_metavar_placeholder "]" ":" num_metavar_placeholder
    load_placeholders: exp_placeholders "[" exp_placeholders "," endian_placeholder "]" ":" num_placeholder

    store_metavars: exp_metavars "with" "[" exp_metavars "," endian_metavar "]" ":" num_metavar "<-" exp_metavars
    store_metavars_placeholders: exp_metavars_placeholders "with" "[" exp_metavars_placeholders "," endian_metavar_placeholder "]" ":" num_metavar_placeholder "<-" exp_metavars_placeholders
    store_placeholders: exp_placeholders "with" "[" exp_placeholders "," endian_placeholder "]" ":" num_placeholder "<-" exp_placeholders
    
    cast_metavars: "cast" ":" cast_metavar ":" num_metavar "[" exp_metavars "]"
    cast_metavars_placeholders: "cast" ":" cast_metavar_placeholder ":" num_placeholder "[" exp_metavars_placeholders "]"
    cast_placeholders: "cast" ":" cast_placeholder ":" num_placeholder "[" exp_placeholders "]"
    
    let_metavars: "let" var_metavars "=" exp_metavars "in" exp_metavars | "let" metavar "=" exp_metavars "in" exp_metavars
    let_metavars_placeholders: "let" var_metavars_placeholders "=" exp_metavars_placeholders "in" exp_metavars_placeholders | "let" metavar "=" exp_metavars_placeholders "in" exp_metavars_placeholders | "let" placeholder "=" exp_metavars_placeholders "in" exp_metavars_placeholders
    let_placeholders: "let" var_placeholders "=" exp_placeholders "in" exp_placeholders | "let" placeholder "=" exp_placeholders "in" exp_placeholders
    
    unknown_metavars: "unknown" "[" string_metavar "]" ":" type_metavars
    unknown_metavars_placeholders: "unknown" "[" string_metavar_placeholder "]" ":" type_metavars_placeholders
    unknown_placeholders: "unknown" "[" string_placeholder "]" ":" type_placeholders
    
    ite_metavars: "ite" exp_metavars exp_metavars exp_metavars
    ite_metavars_placeholders: "ite" exp_metavars_placeholders exp_metavars_placeholders exp_metavars_placeholders
    ite_placeholders: "ite" exp_placeholders exp_placeholders exp_placeholders
    
    extract_metavars: "extract" ":" num_metavar ":" num_metavar "[" exp_metavars "]"
    extract_metavars_placeholders: "extract" ":" num_metavar_placeholder ":" num_metavar_placeholder "[" exp_metavars_placeholders "]"
    extract_placeholders: "extract" ":" num_placeholder ":" num_placeholder "[" exp_metavars_placeholders "]"

    concat_metavars: exp_metavars "@" exp_metavars
    concat_metavars_placeholders: exp_metavars_placeholders "@" exp_metavars_placeholders
    concat_placeholders: exp_placeholders "@" exp_placeholders
    
    // sub is not needed, we can always do the substitution manually
    //sub_metavars: "[" exp_metavars "/" var_metavars "]" exp_metavars
    //sub_metavars_placeholders: "[" exp_metavars_placeholders "/" var_metavars_placeholders "]" exp_metavars_placeholders
    //sub_placeholders: "[" exp_placeholders "/" var_placeholders "]" exp_placeholders

    ///////////

    var_metavars: /[a-zA-Z_][a-zA-Z0-9_]*/ ":" type_metavars
    var_metavars_placeholders: /[a-zA-Z_][a-zA-Z0-9_]*/ ":" type_metavars_placeholders
    var_placeholders: /[a-zA-Z_][a-zA-Z0-9_]*/ ":" type_placeholders
"""

bil_operators = r"""
    bop: aop | lop
    aop: plus | minus | times | divide | s_divide | modulo | s_modulo | bw_and | bw_or | bw_xor | l_shift | r_shift | ar_shift
        plus: "+"
        minus: "-"
        times: "*"
        divide: "/"
        s_divide: "s/"
        modulo: "%"
        s_modulo: "s%"
        bw_and: "&"
        bw_or: "|"
        bw_xor: "xor"
        l_shift: "<<"
        r_shift: ">>"
        ar_shift: ">>>"

    lop: equal | n_equal | less | less_equal | s_less | s_less_equal
        equal: "="
        n_equal: "/="
        less: "<"
        less_equal: "<="
        s_less: "s<"
        s_less_equal: "s<="

    uop: negation | bw_complement
        negation: "--"
        bw_complement: "~"
"""

bil_endian_cast_type = r"""
    endian_metavar: endian | metavar
    endian_metavar_placeholder: endian | metavar | placeholder
    endian_placeholder: endian | metavar | placeholder
    endian: le | be
        le: "le"
        be: "be"

    cast_metavar: cast | metavar
    cast_metavar_placeholder: cast | metavar | placeholder
    cast_placeholder: cast | metavar | placeholder
    cast: low | high | signed | unsigned
        low: "low"
        high: "high"
        signed: "signed"
        unsigned: "unsigned"

    type_metavars: imm_metavars | mem_metavars | metavar
    type_metavars_placeholders: imm_metavars_placeholders | mem_metavars_placeholders | metavar | placeholder
    type_placeholders: imm_placeholders | mem_placeholders | metavar | placeholder
    imm_metavars: "imm" "<" num_metavar ">"
    imm_metavars_placeholders: "imm" "<" num_metavar_placeholder ">"
    imm_placeholders: "imm" "<" num_placeholder ">"
    mem_metavars: "mem" "<" num_metavar "," num_metavar ">"
    mem_metavars_placeholders: "mem" "<" num_metavar_placeholder "," num_metavar_placeholder ">"
    mem_placeholders: "mem" "<" num_placeholder "," num_placeholder ">"
    
    // the type function is unnecessary, if we dont know a type we can use metavars or check the BIL code for any var's type
    //type_func_metavars: "type" "(" val_metavars ")"
    //type_func_metavars_placeholders: "type" "(" val_metavars_placeholders ")"
    //type_func_placeholders: "type" "(" val_placeholders ")"
"""

bil_bitvector_val = r""" // MISSING parentheses and 1:nat (not necessary, same as the first one)
    word_metavars: num_metavar ":" num_metavar | true | false | word_op_metavars
    word_metavars_placeholders: num_metavar_placeholder ":" num_metavar_placeholder | true | false | word_op_metavars_placeholders
    word_placeholders: num_placeholder ":" num_placeholder | true | false | word_op_placeholders
    
    word_op_metavars: word_metavars "bv" aop word_metavars | word_metavars "bv" less word_metavars | word_metavars "bv" s_less word_metavars | "bv" uop word_metavars | word_metavars "bv" dot_concat word_metavars
    word_op_metavars_placeholders: word_metavars_placeholders "bv" aop word_metavars_placeholders | word_metavars_placeholders "bv" less word_metavars_placeholders | word_metavars_placeholders "bv" s_less word_metavars_placeholders | "bv" uop word_metavars_placeholders | word_metavars_placeholders "bv" dot_concat word_metavars_placeholders
    word_op_placeholders: word_placeholders "bv" aop word_placeholders | word_placeholders "bv" less word_placeholders | word_placeholders "bv" s_less word_placeholders | "bv" uop word_placeholders | word_placeholders "bv" dot_concat word_placeholders
        dot_concat: "."
        true: "true"
        false: "false"
    
    // is extend necessary? cant we just do it manually
    //extend_metavars: "ext" word_metavars "~" "hi" ":" num_metavar "~" "lo" ":" num_metavar
    //extend_metavars_placeholders: "ext" word_metavars_placeholders "~" "hi" ":" num_metavar_placeholder "~" "lo" ":" num_metavar_placeholder
    //extend_placeholders: "ext" word_placeholders "~" "hi" ":" num_placeholder "~" "lo" ":" num_placeholder
    
    //sextend_metavars: "sext" word_metavars "~" "hi" ":" num_metavar "~" "lo" ":" num_metavar
    //sextend_metavars_placeholders: "sext" word_metavars_placeholders "~" "hi" ":" num_metavar_placeholder "~" "lo" ":" num_metavar_placeholder
    //sextend_placeholders: "sext" word_placeholders "~" "hi" ":" num_placeholder "~" "lo" ":" num_placeholder

    /////////////
    
    val_metavars: word_metavars | unknown_metavars
    val_metavars_placeholders: word_metavars_placeholders | unknown_metavars_placeholders
    val_placeholders: word_placeholders | unknown_placeholders
"""

modified_bil = rf"""
    {bil_preface}
    {bil_with_metavars}
    {bil_expressions}
    {bil_operators}
    {bil_endian_cast_type}
    {bil_bitvector_val}
""" 

#########################################################

ignored_and_imports = r"""
    %import common.NEWLINE
    %import common.WS
    %ignore WS
    %ignore /\/\/.*/ // for comments
"""

grammar = rf"""
    {csml}
    {predicates}
    {wildcards_placeholders_metavars}
    {modified_bil}
    {ignored_and_imports}
"""

parser = Lark(grammar, start="policy", parser="lalr")

def lint_code(code):
    try:
        parser.parse(code)
        return []  # No errors
    except UnexpectedInput as e:
        return [{
            "line": e.line - 1,
            "column": e.column - 1,
            "message": str(e),
            "severity": 1  # warning
        }]

if __name__ == "__main__":
    code = sys.stdin.read()
    issues = lint_code(code)
    print(json.dumps(issues))