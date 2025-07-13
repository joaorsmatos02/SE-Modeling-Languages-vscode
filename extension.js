const vscode = require('vscode');
const { execSync } = require('child_process');
const { spawn } = require('child_process');
const path = require('path');

////////////////// get python command //////////////////

function getPythonVersion(cmd) {
  try {
    const output = execSync(`${cmd} --version`).toString().trim();
    const match = output.match(/Python (\d+)\.(\d+)\.(\d+)/);
    if (match) {
      const [_, major, minor, patch] = match.map(Number);
      return { cmd, major, minor, patch };
    }
  } catch {
    return null;
  }
  return null;
}

const candidates = ['python3', 'python'] // try both commands
  .map(getPythonVersion)
  .filter(Boolean);

if (candidates.length === 0) {
  throw new Error('Linting is disabled. Neither python3 nor python found on system.');
}

candidates.sort((a, b) => {
  if (a.major !== b.major) return b.major - a.major;
  if (a.minor !== b.minor) return b.minor - a.minor;
  return b.patch - a.patch;
});

const pythonCmd = candidates[0].cmd;

try {
  execSync(`${pythonCmd} -c "import lark"`, { stdio: 'ignore' });
} catch {
  throw new Error(`Linting is disabled. The 'lark' module is not installed for ${pythonCmd}. Please run: ${pythonCmd} -m pip install lark`);
}

////////////////// set up linting //////////////////

const pythonProcesses = new Map(); // key: 'csml' | 'mcml', value: process object

function startLinterProcess(language) {
  const scriptName = language === 'csml' ? 'csml_linter.py' : 'mcml_linter.py';
  const pythonScriptPath = path.join(__dirname, 'linters', scriptName);

  const proc = spawn(pythonCmd, [pythonScriptPath], {
    stdio: ['pipe', 'pipe', 'pipe']
  });

  proc.stderr.on('data', data => {
    console.error(`[${language.toUpperCase()} stderr]`, data.toString());
  });

  return proc;
}

function getLinterProcess(language) {
  if (!pythonProcesses.has(language)) {
    pythonProcesses.set(language, startLinterProcess(language));
  }
  return pythonProcesses.get(language);
}

function lintWithPersistentProcess(language, code, callback) {
  const proc = getLinterProcess(language);
  const message = JSON.stringify({ code }) + '\n';

  let output = '';

  const onData = (data) => {
    output += data.toString();
    if (output.endsWith('\n')) {
      proc.stdout.off('data', onData);
      try {
        const issues = JSON.parse(output.trim());
        callback(null, issues);
      } catch (e) {
        console.error("[Linter] Invalid JSON received:\n", output);
      }
    }
  };

  proc.stdout.on('data', onData);
  proc.stdin.write(message);
}

function runLinter(document, diagnosticCollection) {
  const language = document.languageId;
  if (!['csml', 'mcml'].includes(language)) return;

  const code = document.getText();

  lintWithPersistentProcess(language, code, (err, issues = []) => {
    if (err) {
      console.error(`[${language.toUpperCase()}] Linter error:`, err);
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
}

const debounceMap = new Map();

function registerLinters(context) {
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

  // Lint on Tab Switch (Active Editor Change)
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(editor => {
      if (!editor) return;
      const document = editor.document;
      if (!['csml', 'mcml'].includes(document.languageId)) return;
      runLinter(document, diagnosticCollection);
    })
);
}

////////////////// set up quick fixes //////////////////

class CsmlQuickFixProvider {
  provideCodeActions(document, range, context) {
    const actions = [];

    for (const diagnostic of context.diagnostics) {

        // Quick fix: P[1] -> C
        if (diagnostic.code === 'replace-with-C') {
          const fix = new vscode.CodeAction("Replace with 'C'", vscode.CodeActionKind.QuickFix);
          fix.edit = new vscode.WorkspaceEdit();
          fix.edit.replace(document.uri, diagnostic.range, 'C');
          fix.diagnostics = [diagnostic];
          fix.isPreferred = true;
          actions.push(fix);
        }
  
        // Quick fix: ?unused -> ??
        else if (diagnostic.code === 'replace-with-??') {
          const fix = new vscode.CodeAction("Replace with '??'", vscode.CodeActionKind.QuickFix);
          fix.edit = new vscode.WorkspaceEdit();
          fix.edit.replace(document.uri, diagnostic.range, '??');
          fix.diagnostics = [diagnostic];
          fix.isPreferred = true;
          actions.push(fix);
        }
  
        else if (diagnostic.code === 'universal-rule') {
          const fix = new vscode.CodeAction("Replace with a default rule", vscode.CodeActionKind.QuickFix);
          fix.edit = new vscode.WorkspaceEdit();
          fix.edit.replace(document.uri, diagnostic.range, 'default');
          fix.diagnostics = [diagnostic];
          fix.isPreferred = true;
          actions.push(fix);
        }
      
    }

    return actions;
  }
}

function registerQuickFixes(context) {
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

////////////////// main //////////////////

function activate(context) {
  registerLinters(context)
  registerQuickFixes(context)
}

function deactivate() {
  for (const proc of pythonProcesses.values()) {
    proc.stdin.end();
  }
}

module.exports = {
  activate,
  deactivate
};
