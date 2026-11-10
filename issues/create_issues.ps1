# Creates one GitHub issue per .md file in this folder.
# Requires GitHub CLI (https://cli.github.com) and `gh auth login`.
# First line of each file ("# Title") becomes the issue title; the rest is the body.

$repo = "Aryan886/AlgoTrade"

Get-ChildItem -Path $PSScriptRoot -Filter "*.md" | Sort-Object Name | ForEach-Object {
    $lines = Get-Content -Path $_.FullName -Encoding UTF8
    $title = $lines[0] -replace '^#\s*', ''
    $bodyFile = New-TemporaryFile
    $lines | Select-Object -Skip 1 | Set-Content -Path $bodyFile -Encoding UTF8
    gh issue create --repo $repo --title $title --body-file $bodyFile
    Remove-Item $bodyFile
}
