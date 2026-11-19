param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))

Add-Type -AssemblyName System.Drawing

function New-DutraIcon {
    param([int]$Size, [string]$Name, [double]$Inset = 0.12)
    $bitmap = [System.Drawing.Bitmap]::new($Size, $Size)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $navyColor = [System.Drawing.ColorTranslator]::FromHtml('#102A43')
    $cyanColor = [System.Drawing.ColorTranslator]::FromHtml('#6DB9D6')
    $graphics.Clear($navyColor)

    $scale = $Size * (1 - 2 * $Inset) / 512.0
    $offset = $Size * $Inset
    function X([double]$value) { return [single]($offset + $value * $scale) }
    function W([double]$value) { return [single]($value * $scale) }

    $whitePen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, (W 42))
    $whitePen.StartCap = $whitePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $whitePen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $cyanPen = [System.Drawing.Pen]::new($cyanColor, (W 30))
    $cyanPen.StartCap = $cyanPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $cyanPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $cyan = [System.Drawing.SolidBrush]::new($cyanColor)
    $nodePen = [System.Drawing.Pen]::new($navyColor, (W 8))

    $letter = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $letter.StartFigure()
    $letter.AddLine((X 150), (X 112), (X 238), (X 112))
    $letter.AddBezier((X 238), (X 112), (X 342), (X 112), (X 406), (X 170), (X 406), (X 256))
    $letter.AddBezier((X 406), (X 256), (X 406), (X 342), (X 342), (X 400), (X 238), (X 400))
    $letter.AddLine((X 238), (X 400), (X 150), (X 400))
    $letter.AddLine((X 150), (X 400), (X 150), (X 112))
    $graphics.DrawPath($whitePen, $letter)

    $route = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $route.StartFigure()
    $route.AddLine((X 150), (X 256), (X 248), (X 256))
    $route.AddBezier((X 248), (X 256), (X 294), (X 256), (X 324), (X 230), (X 324), (X 191))
    $graphics.DrawPath($cyanPen, $route)
    foreach ($node in @(@(150, 256), @(324, 191))) {
        $graphics.FillEllipse($cyan, (X ($node[0] - 18)), (X ($node[1] - 18)), (W 36), (W 36))
        $graphics.DrawEllipse($nodePen, (X ($node[0] - 18)), (X ($node[1] - 18)), (W 36), (W 36))
    }

    $target = Join-Path $ProjectRoot "web\assets\$Name"
    $bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
    $route.Dispose(); $letter.Dispose(); $nodePen.Dispose(); $cyan.Dispose(); $cyanPen.Dispose(); $whitePen.Dispose()
    $graphics.Dispose(); $bitmap.Dispose()
}

New-DutraIcon -Size 180 -Name 'apple-touch-icon.png' -Inset 0.12
New-DutraIcon -Size 192 -Name 'icon-192.png' -Inset 0.12
New-DutraIcon -Size 512 -Name 'icon-512.png' -Inset 0.12
New-DutraIcon -Size 512 -Name 'icon-maskable-512.png' -Inset 0.20
