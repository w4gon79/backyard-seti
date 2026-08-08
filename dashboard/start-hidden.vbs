Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "G:\seti"
WshShell.Run "cmd /c ""C:\Users\w4gon\AppData\Local\Programs\Python\Python311\python.exe"" G:\seti\dashboard\app.py", 0, False
