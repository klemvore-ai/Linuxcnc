# LinuxCNC Pro - UI Designer Edition (Mac)

Welcome to the standalone **LinuxCNC Pro UI Editor**. This application allows you to design custom dashboards and operator interfaces for LinuxCNC without needing the full machine control software installed.

## 🚀 Getting Started

### Option A: Running the Standalone Mac App (Recommended)
If you were provided with a `LinuxCNC_UI_Editor.app` file:
1. Simply double-click the `.app` file to launch the application.
2. (Optional) Drag the app to your `Applications` folder.

*Note: If macOS prevents the app from opening because of an "unidentified developer" warning, `Right-Click` (or `Control-Click`) the app and select **Open**.*

### Option B: Running from Python Source
If you are running the source code directly from the `Editor` folder:
1. Open your Mac Terminal.
2. Navigate to this folder.
3. Install the required dependencies:
   ```bash
   pip install PyQt6
   ```
4. Run the application:
   ```bash
   python main.py
   ```
   
### Option C: Compiling your own Mac App
If you want to package the source code into a `.app` bundle yourself:
1. Open your Terminal and make the build script executable: `chmod +x build.command`
2. Double-click **`build.command`** in your Finder. It will automatically install dependencies and build the app into a new `dist` folder.

## 🎨 How to Use the Designer

1. **Layout Editor Tab**: This is your main workspace. 
   - Click an item in the **UI Toolbox** on the left (e.g., Button, DRO, LED).
   - Click **"Place Component"** to drop it onto the canvas.
   - Drag the item around the grid to position it.
   - Grab the bottom-right corner of any item to resize it.
2. **Editing Properties**:
   - **Double-click** (or Right-Click) any block on the canvas to open the Property Editor.
   - Here you can change the text, background color, text color, alignment, and even apply **custom background images**.
3. **Dashboard Preview Tab**:
   - Click this tab to see a live preview of how your UI looks and behaves. 
   - *Note: Machine connections are simulated in this editor.*

## 📤 Sharing Your UI

When you are finished designing your layout, you can easily package it up to send back to the engineering team:

1. Go to the **Layout Editor** tab.
2. Click the **"📤 Export for Sharing"** button in the sidebar.
3. Select an empty folder on your desktop (e.g., "New_CNC_Dashboard").
4. The application will automatically save:
   - The `.linuxcncui` visual layout file.
   - A `.mcomp` logic map file for the engineering team.
   - Copies of any custom images you used.
5. **ZIP** that folder and send it over!