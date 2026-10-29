const path = require("path");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

function activate(context) {
  const serverPath = path.join(__dirname, "lsp_server.py");
  const pythonCmd = process.platform === "win32" ? "python" : "python3";

  const serverOptions = {
    command: pythonCmd,
    args: [serverPath],
    transport: TransportKind.stdio,
  };

  const clientOptions = {
    documentSelector: [{ scheme: "file", language: "lumen" }],
  };

  client = new LanguageClient(
    "lumenLanguageServer",
    "Lumen Language Server",
    serverOptions,
    clientOptions
  );

  client.start();
  context.subscriptions.push(client);
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
