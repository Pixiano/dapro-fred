//main.js
const { app, BrowserWindow } = require('electron');
const path = require('path');

function createWindow() {
  const win = new BrowserWindow({
    width: 1920,
    height: 1080,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
    fullscreen: true, // JARVIS-style fullscreen
    backgroundColor: '#000000',
  });

  win.loadFile(path.join(__dirname, 'index.html'));
  // win.webContents.openDevTools(); // optional: debug console
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});