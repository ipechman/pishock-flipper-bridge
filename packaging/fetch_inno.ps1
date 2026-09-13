[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Destination)

$ErrorActionPreference = 'Stop'
$toolRoot = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
$installer = Join-Path $toolRoot 'innosetup-6.7.3.exe'
$expectedHash = '9C73C3BAE7ED48D44112A0F48E66742C00090BDB5BEF71D9D3C056C66E97B732'
$url = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
if (-not (Test-Path -LiteralPath $installer)) {
    Invoke-WebRequest -Uri $url -OutFile $installer
}
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'The downloaded compiler installer does not match the pinned SHA-256.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Pyrsys B\.V\.') {
    throw 'The compiler installer must have a valid signature from Pyrsys B.V.'
}
$compilerRoot = Join-Path $toolRoot 'inno'
$install = Start-Process -FilePath $installer -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
    '/PORTABLE=1', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/CURRENTUSER',
    '/NOICONS', '/NORESTART', ('/DIR="' + $compilerRoot + '"')
)
if ($install.ExitCode -ne 0) { throw 'Portable Inno Setup extraction failed.' }
Write-Output (Join-Path $compilerRoot 'ISCC.exe')
