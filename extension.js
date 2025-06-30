const vscode = require('vscode');
const { execFile } = require('child_process');
const path = require('path');

const debounceMap = new Map();

function runLinter(document, diagnosticCollection) {
  const language = document.languageId;
  const code = document.getText();

  let scriptName = null;
  if (language === 'csml') {
    scriptName = 'csml_linter.py';
  } else if (language === 'mcml') {
    scriptName = 'mcml_linter.py';
  } else {
    return; // Unsupported language
  }

  const pythonScriptPath = path.join(__dirname, "linters", scriptName);

  const process = execFile('python', [pythonScriptPath], (error, stdout, stderr) => {
    if (error) {
      console.error(`[${language.toUpperCase()}] Python error:\n`, stderr);
      return;
    }

    let issues = [];
    try {
      issues = JSON.parse(stdout);
    } catch (e) {
      console.error('Invalid JSON from linter:', stdout);
      return;
    }

    const diagnostics = issues.map(issue => {
      const range = new vscode.Range(
        issue.line,
        issue.column,
        issue.line,
        issue.column + (issue.length || 1)
      );
      const severity = issue.severity === 1
        ? vscode.DiagnosticSeverity.Warning
        : vscode.DiagnosticSeverity.Error;

      const diagnostic = new vscode.Diagnostic(range, issue.message, severity);
      if (issue.code) {
        diagnostic.code = issue.code;
      }
      return diagnostic;
    });

    diagnosticCollection.set(document.uri, diagnostics);
  });

  process.stdin.write(code);
  process.stdin.end();
}

class CsmlQuickFixProvider {
  provideCodeActions(document, range, context) {
    const actions = [];

    for (const diagnostic of context.diagnostics) {
      // Quick fix: P[1] → C
      if (diagnostic.code === 'replace-with-C') {
        const fix = new vscode.CodeAction("Replace with 'C'", vscode.CodeActionKind.QuickFix);
        fix.edit = new vscode.WorkspaceEdit();
        fix.edit.replace(document.uri, diagnostic.range, 'C');
        fix.diagnostics = [diagnostic];
        fix.isPreferred = true;
        actions.push(fix);
      }

      // Quick fix: ?var → ??
      else if (diagnostic.code === 'replace-with-??') {
        const fix = new vscode.CodeAction("Replace with '??'", vscode.CodeActionKind.QuickFix);
        fix.edit = new vscode.WorkspaceEdit();
        fix.edit.replace(document.uri, diagnostic.range, '??');
        fix.diagnostics = [diagnostic];
        fix.isPreferred = true;
        actions.push(fix);
      }
    }

    return actions;
  }
}

function activate(context) {
  const diagnosticCollection = vscode.languages.createDiagnosticCollection('csml-mcml');

  // Lint on Save
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(document => {
      if (!['csml', 'mcml'].includes(document.languageId)) return;
      runLinter(document, diagnosticCollection);
    })
  );

  // Debounced Lint on Type (Change)
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(event => {
      const document = event.document;
      const language = document.languageId;

      if (!['csml', 'mcml'].includes(language)) return;

      const uri = document.uri.toString();
      clearTimeout(debounceMap.get(uri));

      const timeout = setTimeout(() => {
        runLinter(document, diagnosticCollection);
        debounceMap.delete(uri);
      }, 300); // 300ms delay after last keystroke

      debounceMap.set(uri, timeout);
    })
  );

  // Lint All Open Documents on Startup
  vscode.workspace.textDocuments.forEach(document => {
    if (!['csml', 'mcml'].includes(document.languageId)) return;
    runLinter(document, diagnosticCollection);
  });

  // Register Quick Fix provider
  context.subscriptions.push(
    vscode.languages.registerCodeActionsProvider(
      ['csml', 'mcml'],
      new CsmlQuickFixProvider(),
      {
        providedCodeActionKinds: [vscode.CodeActionKind.QuickFix]
      }
    )
  );
}

function deactivate() {}

module.exports = {
  activate,
  deactivate
};
