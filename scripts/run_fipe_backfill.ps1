$projeto = "D:\indice-gsa-veicular"
$python = "$projeto\venv\Scripts\python.exe"
$script = "$projeto\scripts\ingestao\fipe_backfill_historico.py"
$log = "$projeto\logs\execucao_backfill_$(Get-Date -Format 'yyyy-MM-dd').log"

Set-Location $projeto
& $python $script *>> $log
