param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))

Add-Type -AssemblyName System.Drawing

function New-ConradoIcon {
    param([int]$Size, [string]$Name, [double]$Inset = 0.12)
    $bitmap = [System.Drawing.Bitmap]::new($Size, $Size)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#102847'))

    $scale = $Size * (1 - 2 * $Inset) / 512.0
    $offset = $Size * $Inset
    function X([double]$value) { return [single]($offset + $value * $scale) }
    function W([double]$value) { return [single]($value * $scale) }

    $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $cyan = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#63b9e9'))
    $bluePen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#1769aa'), (W 24))
    $bluePen.StartCap = $bluePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $wheel = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#102847'))
    $wheelPen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, (W 18))

    $graphics.FillRectangle($white, (X 55), (X 135), (W 258), (W 213))
    $cab = [System.Drawing.PointF[]]@(
        [System.Drawing.PointF]::new((X 313), (X 205)),
        [System.Drawing.PointF]::new((X 384), (X 205)),
        [System.Drawing.PointF]::new((X 457), (X 286)),
        [System.Drawing.PointF]::new((X 457), (X 348)),
        [System.Drawing.PointF]::new((X 313), (X 348))
    )
    $graphics.FillPolygon($cyan, $cab)
    $window = [System.Drawing.PointF[]]@(
        [System.Drawing.PointF]::new((X 342), (X 231)),
        [System.Drawing.PointF]::new((X 374), (X 231)),
        [System.Drawing.PointF]::new((X 414), (X 280)),
        [System.Drawing.PointF]::new((X 342), (X 280))
    )
    $graphics.FillPolygon($white, $window)
    $graphics.DrawLine($bluePen, (X 101), (X 196), (X 259), (X 196))
    $graphics.DrawLine($bluePen, (X 101), (X 247), (X 225), (X 247))
    foreach ($center in @(142, 372)) {
        $graphics.FillEllipse($wheel, (X ($center - 38)), (X 320), (W 76), (W 76))
        $graphics.DrawEllipse($wheelPen, (X ($center - 38)), (X 320), (W 76), (W 76))
    }

    $target = Join-Path $ProjectRoot "web\assets\$Name"
    $bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
    $wheelPen.Dispose(); $wheel.Dispose(); $bluePen.Dispose(); $cyan.Dispose(); $white.Dispose()
    $graphics.Dispose(); $bitmap.Dispose()
}

New-ConradoIcon -Size 180 -Name 'apple-touch-icon.png' -Inset 0.12
New-ConradoIcon -Size 192 -Name 'icon-192.png' -Inset 0.12
New-ConradoIcon -Size 512 -Name 'icon-512.png' -Inset 0.12
New-ConradoIcon -Size 512 -Name 'icon-maskable-512.png' -Inset 0.20
