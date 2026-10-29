// The Electron app of the HTML questionnaire: one window with the page whose URL is the last argument.
const { app, BrowserWindow, Menu } = require("electron");

const url = process.argv[process.argv.length - 1];

// No menu bar, and so none of its shortcuts, such as reloading the page or opening the developer tools.
Menu.setApplicationMenu(null);

app.whenReady().then(() => {
  const window = new BrowserWindow({ width: 1000, height: 800, title: "Questionnaire" });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.loadURL(url);
});

app.on("window-all-closed", () => app.quit());
