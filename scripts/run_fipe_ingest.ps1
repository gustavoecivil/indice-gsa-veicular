$projeto = "C:\Users\gusta\indice-gsa-veicular"
$python = "$projeto\venv\Scripts\python.exe"
$script = "$projeto\scripts\ingestao\fipe.py"
$log = "$projeto\logs\execucao_$(Get-Date -Format 'yyyy-MM-dd').log"

Set-Location $projeto
& $python $script *>> $log
