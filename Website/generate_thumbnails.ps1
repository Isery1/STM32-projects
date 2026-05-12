Add-Type -AssemblyName System.Drawing

function Resize-Image {
    param (
        [string]$SourcePath,
        [string]$TargetPath,
        [int]$MaxWidth = 1200
    )
    try {
        $image = [System.Drawing.Image]::FromFile($SourcePath)
        
        # Handle EXIF orientation metadata (PropertyId 0x0112)
        try {
            $orientationProp = $image.GetPropertyItem(0x0112)
            $orientation = [BitConverter]::ToUInt16($orientationProp.Value, 0)
            switch ($orientation) {
                2 { $image.RotateFlip([System.Drawing.RotateFlipType]::RotateNoneFlipX) }
                3 { $image.RotateFlip([System.Drawing.RotateFlipType]::Rotate180FlipNone) }
                4 { $image.RotateFlip([System.Drawing.RotateFlipType]::RotateNoneFlipY) }
                5 { $image.RotateFlip([System.Drawing.RotateFlipType]::Rotate270FlipX) }
                6 { $image.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipNone) }
                7 { $image.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipX) }
                8 { $image.RotateFlip([System.Drawing.RotateFlipType]::Rotate270FlipNone) }
            }
        } catch {
            # No EXIF orientation tag found, no rotation needed
        }
        
        $ratio = $image.Height / $image.Width
        $newWidth = $image.Width
        $newHeight = $image.Height
        
        if ($image.Width -gt $MaxWidth) {
            $newWidth = $MaxWidth
            $newHeight = [int]($MaxWidth * $ratio)
        }
        
        $bitmap = New-Object System.Drawing.Bitmap($newWidth, $newHeight)
        $graphic = [System.Drawing.Graphics]::FromImage($bitmap)
        
        $graphic.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphic.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphic.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphic.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        
        $graphic.DrawImage($image, 0, 0, $newWidth, $newHeight)
        
        # JPEG Encoder
        $encoder = [System.Drawing.Imaging.ImageCodecInfo]::GetImageDecoders() | Where-Object { $_.FormatID -eq [System.Drawing.Imaging.ImageFormat]::Jpeg.Guid }
        $encoderParams = New-Object System.Drawing.Imaging.EncoderParameters(1)
        $encoderParams.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, 90) # 90% quality is perfect for professional web previews
        
        $bitmap.Save($TargetPath, $encoder, $encoderParams)
        
        $graphic.Dispose()
        $bitmap.Dispose()
        $image.Dispose()
        Write-Host "Created high-quality preview: $(Split-Path $SourcePath -Leaf)" -ForegroundColor Green
    } catch {
        Write-Host "Could not process $SourcePath : $_" -ForegroundColor Yellow
    }
}

# Process both image folders
$folders = @("Babyfotos D&T", "Fotos")

foreach ($folder in $folders) {
    $srcDir = Join-Path $PSScriptRoot $folder
    if (Test-Path $srcDir) {
        Write-Host "Generating high-quality previews for: $folder..." -ForegroundColor Cyan
        
        $files = Get-ChildItem -Path $srcDir -File -Include "*.jpg", "*.jpeg", "*.png", "*.JPG", "*.PNG" -Recurse | Where-Object { $_.FullName -notmatch "thumbnails" }
        
        foreach ($file in $files) {
            $destDir = Join-Path $file.DirectoryName "thumbnails"
            if (!(Test-Path $destDir)) {
                New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            }
            
            $targetPath = Join-Path $destDir $file.Name
            # Always regenerate to overwrite the low-quality versions
            Resize-Image -SourcePath $file.FullName -TargetPath $targetPath
        }
    }
}

Write-Host "All thumbnails successfully generated!" -ForegroundColor Green
