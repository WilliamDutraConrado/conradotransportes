param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SourceImage = ""
)

Add-Type -AssemblyName System.Drawing

if (-not $SourceImage) {
    $SourceImage = Join-Path $ProjectRoot "web\assets\emblema-dutralog-master.png"
}
$resolvedSource = (Resolve-Path -LiteralPath $SourceImage).Path

function New-DutraIcon {
    param([int]$Size, [string]$Name)

    $source = [System.Drawing.Image]::FromFile($resolvedSource)
    try {
        if ($source.Width -ne $source.Height) {
            throw "A imagem-mestre precisa ser quadrada; recebida: $($source.Width)x$($source.Height)."
        }
        $bitmap = [System.Drawing.Bitmap]::new($Size, $Size)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.Clear([System.Drawing.Color]::FromArgb(14, 36, 54))
                $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
                $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
                $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
                $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
                $graphics.DrawImage($source, 0, 0, $Size, $Size)
            }
            finally {
                $graphics.Dispose()
            }
            $target = Join-Path $ProjectRoot "web\assets\$Name"
            $bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally {
            $bitmap.Dispose()
        }
    }
    finally {
        $source.Dispose()
    }
}

New-DutraIcon -Size 180 -Name 'dutralog-apple-touch-icon.png'
New-DutraIcon -Size 192 -Name 'dutralog-icon-192.png'
New-DutraIcon -Size 512 -Name 'dutralog-icon-512.png'
New-DutraIcon -Size 512 -Name 'dutralog-icon-maskable-512.png'

# Compatibilidade com a versão anterior durante a atualização do service worker.
New-DutraIcon -Size 180 -Name 'apple-touch-icon.png'
New-DutraIcon -Size 192 -Name 'icon-192.png'
New-DutraIcon -Size 512 -Name 'icon-512.png'
New-DutraIcon -Size 512 -Name 'icon-maskable-512.png'

Write-Host "Ícones oficiais gerados a partir de $resolvedSource"
