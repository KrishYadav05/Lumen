# docs/grammar.md

# Lumen Language Specification — Chapter 2: Formal Grammar

Status: **Normative.** Revised for Language Specification **3.1** (Phase 4); the productions that 3.1
added or changed are marked *(3.1)*. Publisher: Krish Kumar. EBNF notation: `|` alternation, `[...]` optional, `{...}`
zero-or-more repetition, literal terminals in `'...'`. `INDENT`/`DEDENT`/`NEWLINE` are
lexer-synthesized tokens per `language.md` §1.5. This grammar is a faithful
transcription of the recursive-descent parser that is the reference implementation;
where the parser's actual behavior and a "cleaner" grammar would differ (e.g.
non-chaining comparisons), this document follows the parser.

## 2.1 Program Structure

```ebnf
program        = { NEWLINE } , { statement , { NEWLINE } } , EOF ;

block          = { NEWLINE } , INDENT , { NEWLINE } ,
                  statement , { NEWLINE } , { statement , { NEWLINE } } ,
                  DEDENT ;
```

## 2.2 Statements

```ebnf
statement      = let_stmt | print_stmt | if_stmt | for_stmt | while_stmt
               | fn_decl | return_stmt | struct_decl | enum_decl | match_stmt
               | try_stmt | use_stmt | assign_stmt | expr_stmt ;

let_stmt       = 'let' , NAME , [ ':' , type ] , '=' , expression ;          (* 3.1: a full type *)

assign_stmt    = assign_target , '=' , expression ;
assign_target  = NAME | field_access | index_expr ;
               (* parser accepts any primary-with-trailers as the LHS and
                  requires, at the point of assignment, that it reduce to
                  one of: bare NAME, a '.'-field-access, or a '['-index;
                  any other LHS shape is a syntax error *)

print_stmt     = 'print' , expression ;

if_stmt        = 'if' , expression , ':' , block ,
                  [ 'else' , ':' , block ] ;

for_stmt       = 'for' , NAME , 'in' , expression , ':' , block ;

while_stmt     = 'while' , expression , ':' , block ;
               (* added post-1.0 (Phase 3, Language Specification 2.2). No
                  'break'/'continue' exist, so the only way out of a while
                  loop is for its own condition to become false, or for a
                  'return' to unwind the enclosing function entirely *)

fn_decl        = 'fn' , NAME , [ type_params ] , '(' , [ param_list ] , ')' ,
                  [ '->' , type ] , ':' , block ;                             (* 3.1: a full type *)
type_params    = '<' , NAME , { ',' , NAME } , '>' ;
param_list     = param , { ',' , param } ;
param          = NAME , [ ':' , type ] ;                                      (* 3.1: a full type *)

type           = NAME , [ '<' , type , { ',' , type } , '>' ] ;               (* 3.1 *)
               (* `Int`, `Tree<Int>`, `Result<Option<Int>, Str>`. A type is stored as text and
                  checked by the static pass (types.md §3.8); the wrong number of arguments,
                  or a generic type nothing declares, is a static error (E0202, E0203) *)

return_stmt    = 'return' , expression ;
               (* a bare 'return' with no expression is NOT valid grammar *)

struct_decl    = 'struct' , NAME , [ type_params ] , ':' ,                   (* 3.1: type_params *)
                  { NEWLINE } , INDENT , { NEWLINE } ,
                  field , { NEWLINE } , { field , { NEWLINE } } ,
                  DEDENT ;
field          = NAME , [ ':' , type ] ;                                     (* 3.1: optional type *)
               (* each indented line is one field: a bare name (unchecked, as before 3.1) or a
                  name with a type *)

enum_decl      = 'enum' , NAME , [ type_params ] , ':' ,                     (* 3.1: type_params *)
                  { NEWLINE } , INDENT , { NEWLINE } ,
                  variant , { NEWLINE } , { variant , { NEWLINE } } ,
                  DEDENT ;
variant        = NAME , [ '(' , payload , { ',' , payload } , ')' ] ;
payload        = NAME                       (* a label, unchecked — the 1.0–3.0 form *)
               | NAME , ':' , type          (* 3.1: a label with a type *)
               | type ;                     (* 3.1: a bare type *)
               (* a variant with no '(...)' is a unit variant. A bare NAME is read as a type
                  when it is a built-in type, one of the enum's type parameters or a struct/enum
                  the file declares (or when '<' follows it); otherwise it is a label *)

match_stmt     = 'match' , expression , ':' ,
                  { NEWLINE } , INDENT , { NEWLINE } ,
                  match_case , { NEWLINE } , { match_case , { NEWLINE } } ,
                  DEDENT ;
match_case     = pattern , ':' , block ;

try_stmt       = 'try' , ':' , block ,
                  ( 'catch' , NAME , ':' , block , [ 'finally' , ':' , block ]
                  | 'finally' , ':' , block ) ;                               (* 3.1: finally *)
               (* a 'try' needs a 'catch', a 'finally', or both. 'return' and '?' are not allowed
                  inside the finally block (E0407) — errors.md §4.6 *)

use_stmt       = 'use' , NAME , { '.' , NAME }               (* capability declaration (top level only) *)
               | 'use' , STRING , 'as' , NAME ;             (* module import *)

expr_stmt      = expression ;
               (* an expression evaluated purely for side effect, e.g. a
                  bare function call; its value is discarded *)
```

## 2.3 Patterns (used only inside `match_case`)

```ebnf
pattern        = NUMBER                        (* literal-equality pattern *)
               | STRING                        (* literal-equality pattern *)
               | 'true' | 'false'              (* literal-equality pattern *)
               | '_'                           (* wildcard: matches anything, binds nothing *)
               | NAME , '(' , NAME , { ',' , NAME } , ')'   (* variant pattern *)
               | NAME ;                        (* unit-variant-or-bind pattern, resolved
                                                    at match time — see errors.md §4.5 *)
```

## 2.4 Expressions — precedence-climbing production chain

This chain directly encodes the precedence table in `language.md` §1.10. Each
production only descends to the next-tighter production for its operands, which is
what produces left-associativity for every binary level.

```ebnf
expression     = or_expr ;

or_expr        = and_expr , { 'or' , and_expr } ;
and_expr       = not_expr , { 'and' , not_expr } ;
not_expr       = 'not' , not_expr | comparison ;
comparison     = additive , [ comp_op , additive ] , { comp_op , additive } ;
comp_op        = '==' | '!=' | '<' | '>' | '<=' | '>=' ;
additive       = multiplicative , { ( '+' | '-' ) , multiplicative } ;
multiplicative = unary , { ( '*' | '/' | '%' ) , unary } ;
unary          = '-' , unary | postfix ;
postfix        = primary , { '?' , trailer_chain } ;                          (* 3.1 *)
               (* `expr?` — the postfix propagation operator, at the same level as '.', '[' and '(';
                  trailers may follow it: `wrap(a)?.value`. It is a syntax error directly after an
                  argument of a paren-free call (`f x?`, ambiguous) — write `(f x)?` or `f(x)?`;
                  its typing and control flow are errors.md §4.2.3 *)
```

Note on `comparison`: although written with an explicit `{ comp_op, additive }` tail
(inherited directly from the reference parser's `while` loop), the semantic effect is
non-chaining left-associative folding, i.e. `a < b < c` parses as `(a < b) < c` — see
`language.md` §1.10.

## 2.5 Primary Expressions and Trailers

```ebnf
primary        = 'spawn' , call_expr                  (* call_expr only — see concurrency.md §6.2 *)
               | NUMBER
               | STRING
               | 'true' | 'false'
               | list_lit
               | dict_lit
               | struct_lit
               | call_expr
               | NAME                                  (* bare variable reference *)
               | '(' , expression , ')' ;

primary        = primary_base , trailer_chain ;
trailer_chain  = { '.' , NAME | '[' , expression , ']' } ,
                  [ '(' , [ arg_list ] , ')' ] ;
               (* the trailing '(' call form is only valid when the
                  immediately preceding trailer was a '.' field access —
                  i.e. Lumen supports `module.fn(args)` call syntax but NOT
                  calling the result of an arbitrary expression; `a[0]()`
                  is a syntax/semantic error because `[0]` is not a '.' trailer *)

call_expr      = NAME , '(' , [ arg_list ] , ')' ;
arg_list       = expression , { ',' , expression } ;

list_lit       = '[' , [ expression , { ',' , expression } ] , ']' ;
dict_lit       = '{' , [ dict_entry , { ',' , dict_entry } ] , '}' ;
dict_entry     = expression , ':' , expression ;

struct_lit     = NAME , '{' , [ field_init , { ',' , field_init } ] , '}' ;
field_init     = NAME , ':' , expression ;
```

### 2.5.1 Grammatical ambiguity resolution: `{` after a `NAME`

`NAME '{'` is resolved eagerly and unconditionally as the start of a `struct_lit`
production — the parser does not look ahead to disambiguate against, say, a `match`
body or a block. This means a bare `NAME` immediately followed by `{` on the same
logical position can never be "a name, then separately a dict/block" — it is always
struct-literal syntax. This is enforced at the lexical/parse boundary and is not
configurable.

### 2.5.2 Call-vs-variant disambiguation

`call_expr` (`NAME '(' ... ')'`) is a single, uniform grammar production used for four
semantically distinct operations, disambiguated at evaluation time, not parse time
(see `errors.md` §4.4 for the resolution order): an ordinary user function call, an
`enum` variant constructor, a `spawn`-wrapped call, and one of the fixed built-in names
in `standard-library.md`.

## 2.6 Complete Worked Grammar Derivation

```lumen
fn area<T>(shape) -> Float:
    match shape:
        Circle(r):
            return 3.14159 * r * r
        Square(s):
            return s * s
```

derives as: `fn_decl` → `type_params` = `<T>` → `param_list` = `shape` (no type
annotation, i.e. `Any`) → `'->' type` = `Float` → `block` containing one `match_stmt`
→ two `match_case`s, each a `pattern` of form `NAME '(' NAME ')'` (variant pattern)
followed by a `block` containing one `return_stmt` whose `expression` is a
`multiplicative` chain over `primary` operands.

## 2.7 The 3.1 Productions in Use

Every production marked *(3.1)* above appears in the programs below, which `tests/test_docs.py` runs on
all three execution paths (the output shown is the output produced).

```lumen run
struct Pair<A, B>:
    first: A
    second: B

enum Shape:
    Circle(radius: Int)
    Rect(Int, Int)
    Dot

fn area(s: Shape) -> Option<Int>:
    match s:
        Circle(r):
            return Some(3 * r * r)
        Rect(w, h):
            return Some(w * h)
        Dot:
            return None

fn total(a: Shape, b: Shape) -> Option<Int>:
    let x: Int = area(a)?
    let y: Int = area(b)?
    return Some(x + y)

let pair: Pair<Int, Str> = Pair { first: 1, second: "one" }
print pair.second
print total(Circle(1), Rect(2, 3))
print total(Circle(1), Dot)
```
```output
one
Some(9)
None
```

`try` with both a `catch` and a `finally` (the `finally` runs once on every way out of the `try`):

```lumen run
fn risky(n):
    if n > 1:
        fail("too big")
    return n

fn attempt(n):
    try:
        return risky(n)
    catch e:
        print "caught: " + e.message
        return -1
    finally:
        print "done with " + n

print attempt(1)
print attempt(2)
```
```output
done with 1
1
caught: too big
done with 2
-1
```

---
