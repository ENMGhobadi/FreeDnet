Set sh = CreateObject("WScript.Shell")
rc = sh.Run("schtasks.exe /Run /TN ""FreeDnetPro""", 0, True)
If rc <> 0 Then
    sh.Run """" & Replace(WScript.ScriptFullName, "launch.vbs", "FreeDnet.exe") & """", 1, False
End If
