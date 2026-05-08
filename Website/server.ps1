$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add('http://localhost:9090/')
$listener.Start()
Write-Host 'Server running on http://localhost:9090/'

while ($true) {
    $ctx = $listener.GetContext()
    $path = $ctx.Request.Url.LocalPath
    if ($path -eq '/') { $path = '/index.html' }
    
    $decodedPath = [System.Uri]::UnescapeDataString($path)
    $file = Join-Path 'c:\Users\hette\STM32CubeIDE\workspace_1.19.0\Website' ($decodedPath.TrimStart('/').Replace('/', '\'))
    
    if (Test-Path $file) {
        $bytes = [System.IO.File]::ReadAllBytes($file)
        $ext = [System.IO.Path]::GetExtension($file).ToLower()
        $mime = switch ($ext) {
            '.html' { 'text/html; charset=utf-8' }
            '.css'  { 'text/css' }
            '.js'   { 'application/javascript' }
            '.jpg'  { 'image/jpeg' }
            '.jpeg' { 'image/jpeg' }
            '.png'  { 'image/png' }
            default { 'application/octet-stream' }
        }
        $ctx.Response.ContentType = $mime
        $ctx.Response.ContentLength64 = $bytes.Length
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    } else {
        $ctx.Response.StatusCode = 404
        Write-Host "404: $file"
    }
    $ctx.Response.Close()
}
