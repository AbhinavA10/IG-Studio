# PyInstaller build for a single portable IGStudio.exe.
#   conda activate igstudio
#   pyinstaller ig_studio.spec --noconfirm --clean
# Output: dist\IGStudio.exe
a = Analysis(
    ["ig_studio.py"],
    datas=[("ig-studio.html", ".")],
    hiddenimports=["tkinter", "tkinter.filedialog"],  # folder dialog
    excludes=["numpy", "matplotlib", "pytest", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="IGStudio",
    icon="assets/igstudio.ico",
    console=True,  # the console window shows status; closing it quits IG Studio
    upx=False,     # UPX-compressed exes trigger more antivirus false positives
)
