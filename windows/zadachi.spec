# PyInstaller spec (used by GitHub Actions and build.bat)
# One self-contained .exe, no Python required on the target PC.
a = Analysis(['Zadachi.py'], pathex=['.'], binaries=[], datas=[('icon.ico', '.'), ('icon.png', '.')],
             hiddenimports=[], hookspath=[], runtime_hooks=[],
             excludes=['test', 'unittest'], noarchive=False)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name='Zadachi', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, icon='icon.ico')
