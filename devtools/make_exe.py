"""Собрать Shikigami.exe: двойной щелчок — и программа запущена, без консоли и bat-файлов.

1. shell/node_modules/electron/dist/Shikigami.exe — копия electron.exe с иконкой лисы и описанием «Shikigami»:
   так она отдельной строкой «Shikigami» в микшере громкости (можно приглушить только её) и в диспетчере задач.
2. Shikigami.exe в корне проекта — крошечная запускалка (C#, компилятор есть в любой Windows), стартует пункт 1.
3. Ярлык «Shikigami» на рабочем столе.

После переустановки node_modules (обновление Electron) — запустить скрипт снова:
    .venv\\Scripts\\python.exe devtools\\make_exe.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "shell" / "node_modules" / "electron" / "dist"
_dev = ROOT / "data" / "dev-paths.json"
RCEDIT = Path(json.loads(_dev.read_text(encoding="utf-8"))["rcedit"] if _dev.exists() else "rcedit-x64.exe")   # npm i rcedit
CSC = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
ICO = ROOT / "shell" / "assets" / "shikigami.ico"

LAUNCHER = r'''
using System; using System.Linq; using System.Diagnostics; using System.IO; using System.Reflection; using System.Windows.Forms;
[assembly: AssemblyTitle("Shikigami")] [assembly: AssemblyProduct("Shikigami")] [assembly: AssemblyVersion("1.0.0.0")]
class Launcher {
    [STAThread] static void Main() {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string shell = Path.Combine(root, "shell");
        string dist = Path.Combine(shell, @"node_modules\electron\dist");
        string exe = Path.Combine(dist, "Shikigami.exe");
        if (!File.Exists(exe)) exe = Path.Combine(dist, "electron.exe");
        if (!File.Exists(exe)) { MessageBox.Show("Не найден Electron:\n" + dist, "Shikigami"); return; }
        string args = "\"" + shell + "\"";
        foreach (string a in Environment.GetCommandLineArgs().Skip(1)) args += " \"" + a + "\"";  // например --mute-audio для тестов
        Process.Start(new ProcessStartInfo(exe, args) { WorkingDirectory = shell, UseShellExecute = false });
    }
}
'''


def run(*a):
    r = subprocess.run([str(x) for x in a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit(f"ошибка: {a[0]}\n{r.stdout}\n{r.stderr}")


def main():
    Image.open(ROOT / "shell" / "assets" / "icon256.png").save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("иконка:", ICO)

    exe = DIST / "Shikigami.exe"
    shutil.copy2(DIST / "electron.exe", exe)
    run(RCEDIT, exe, "--set-icon", ICO, "--set-version-string", "FileDescription", "Shikigami",
        "--set-version-string", "ProductName", "Shikigami", "--set-version-string", "OriginalFilename", "Shikigami.exe",
        "--set-version-string", "InternalName", "Shikigami", "--set-version-string", "CompanyName", "Shikigami contributors")
    print("оболочка:", exe)

    src = ROOT / "devtools" / "_launcher.cs"
    src.write_text(LAUNCHER, encoding="utf-8-sig")
    run(CSC, "/nologo", "/target:winexe", "/codepage:65001", "/optimize", f"/win32icon:{ICO}", "/r:System.Windows.Forms.dll",
        f"/out:{ROOT / 'Shikigami.exe'}", src)
    src.unlink()
    print("запускалка:", ROOT / "Shikigami.exe")

    ps = ("$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\Shikigami.lnk\"); "
          f"$s.TargetPath='{ROOT / 'Shikigami.exe'}'; $s.WorkingDirectory='{ROOT}'; $s.IconLocation='{ROOT / 'Shikigami.exe'},0'; "
          "$s.Description='Shikigami — AI desktop companion'; $s.Save(); \"$d\\Shikigami.lnk\"")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    print("ярлык:", r.stdout.decode("cp866", "replace").strip() or r.stderr.decode("cp866", "replace").strip())


if __name__ == "__main__":
    main()
