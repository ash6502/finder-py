# finder-py
System 7 Finder implemented in Python.

A working file manager that looks and behaves like the classic Macintosh
System 7 Finder: lavender folder icons, the "items / in disk / available"
header, patterned scroll bars with arrow boxes, the grow box, and System 7
style dialogs. The window title is always the name of the folder you're in.

It only uses the Python standard library (Tkinter), and runs on Linux, macOS
and Windows.

## Running

```sh
python3 finder.py            # opens the current directory
python3 finder.py ~/Documents
```

If Tkinter is missing on Linux, install it with `sudo apt install python3-tk`
(Debian/Ubuntu) or `sudo dnf install python3-tkinter` (Fedora).

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| Ctrl+N | New file, then type its name |
| Ctrl+Shift+N | New folder, then type its name |
| F2 | Rename the selected item |
| Ctrl+T | Open the current folder in a terminal |
| Double-click / Return / Ctrl+O | Open (files ask which program to use) |
| F1 | Help |
| Ctrl+C / Ctrl+X / Ctrl+V | Copy / Cut / Paste |
| Ctrl+D | Duplicate |
| Delete | Move to Trash |
| Shift+Delete | Delete permanently (asks first) |
| Ctrl+I | Get Info |
| Backspace / Ctrl+Up | Go to the enclosing folder |
| Alt+Left / Alt+Right | Back / Forward |
| Ctrl+L | Go to a folder by typing its path |
| Ctrl+Shift+H | Home folder |
| Ctrl+1 / Ctrl+2 | View by Icon / by Name |
| Ctrl+H | Show or hide hidden files |
| Ctrl+A / Escape | Select all / Deselect |
| F5 / Ctrl+R | Refresh |
| Arrow keys, typing a name | Move the selection |
| Ctrl+W / Ctrl+Q | Quit |

On macOS, Cmd works wherever Ctrl is listed.

## Notes

- **Open With:** double-clicking a file shows a list of installed programs
  (read from `.desktop` files on Linux, `/Applications` on macOS). Programs that
  handle the file's type are listed first. You can also type any command; `{}`
  stands for the file's path. The last choice for each file extension is
  pre-selected next time.
- **Terminal:** Ctrl+T uses `$TERMINAL` if set, otherwise the first terminal it
  finds (gnome-terminal, konsole, xfce4-terminal, kitty, alacritty, xterm, ...).
  You can also set `"terminal"` in the config file.
- **Trash:** on Linux the freedesktop.org Trash (`~/.local/share/Trash`) is used,
  so items show up in your desktop's trash too; on macOS, `~/.Trash`. On Windows,
  installing the optional `send2trash` package enables the Recycle Bin, and
  without it Delete asks before deleting permanently.
- **Icons** can be dragged around the window or dropped onto a folder to move
  items into it. Special ▸ Clean Up Window puts them back on the grid.
- Settings (view, hidden files, icon positions, Open With choices) are saved in
  `~/.config/finder-py/config.json` (`~/Library/Application Support/finder-py`
  on macOS, `%APPDATA%\finder-py` on Windows).
- For the full effect, install a Chicago-style font (e.g. ChicagoFLF) and a
  Geneva-style font; the Finder uses them when they're present and falls back
  to Helvetica/Arial otherwise.
