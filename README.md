# SE Modeling Languages Linting and Syntax Highlighting

This Visual Studio Code extension provides syntax highlighting for the CSml and MCml symbolic execution DSLs. To use it, open a `.csml` or `.mcml` file and enjoy enhanced highlighting.

Will default to the most recent `python` installation found when being used and depends on `lark` `(pip install lark)`.

## Installation

Run `vsce package` on this folder, then:
```
code --install-extension <generated-file>
```
Requires `npm`, `python`, and `vsce` `(npm install -g vsce)` to build. 

