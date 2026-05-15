<?php
/**
 * Lightweight RFID PHP Backend
 * Implements:
 *  - JWT Token Authentication (HS256)
 *  - Secure JSON Scan Submission
 *  - Beautiful Modern Visual Web Dashboard
 */

// ================= CONFIGURATION =================
// Server timezone for “today”, punch dates, and worked-time totals (PHP date_*)
date_default_timezone_set(getenv('RFID_APP_TZ') ?: 'Europe/Vienna');

// Global Shared Secret used to enroll and authorize ANY device
$GLOBAL_SHARED_SECRET = "Test123";

$JWT_SECRET_KEY = "super-secret-random-signing-key-123";

// File where scans will be stored
$LOG_FILE = __DIR__ . '/scans.log';

// File where dynamic device registries are stored
$DEVICES_FILE = __DIR__ . '/devices.json';
// Optional: map RFID uid -> display name for greetings & dashboard (not deleted by Clear Logs)
$EMPLOYEES_FILE = __DIR__ . '/employees.json';
// =================================================

/** Normalize badge UID for keys, log rows, and employee map lookup (JSON int/float/scientific vs string). */
function normalize_uid($uid) {
    if ($uid === null || $uid === '') {
        return '';
    }
    if (is_int($uid)) {
        return (string)$uid;
    }
    if (is_float($uid)) {
        if (!is_finite($uid)) {
            return '';
        }
        return sprintf('%.0f', $uid);
    }
    $s = trim((string)$uid);
    if ($s === '') {
        return '';
    }
    // json_decode large numbers as string "2.5E+11" / numeric strings
    if (is_numeric($s)) {
        $f = (float)$s;
        if (is_finite($f)) {
            $s = sprintf('%.0f', $f);
        }
    }
    // Plain integer badge string (strip optional .000)
    if (preg_match('/^(\d+)(?:\.0+)?$/', $s, $m)) {
        return $m[1];
    }
    return $s;
}

function load_devices($file) {
    if (!file_exists($file)) return [];
    $raw = @file_get_contents($file);
    return json_decode($raw, true) ?? [];
}

// Helper: Safe Save JSON Device Database
function save_devices($file, $data) {
    file_put_contents($file, json_encode($data, JSON_PRETTY_PRINT), LOCK_EX);
}

/**
 * Parse scans.log (newest rows first in ['scans'], authoritative per-UID state in ['last_by_uid']).
 * Log line format: server_time,device_id,uid,event[,client_local_time]
 * Legacy 3-field lines are treated as event "in".
 */
function parse_scans_log_file($logFile) {
    $lastByUid = [];
    $rowsChrono = [];
    if (!file_exists($logFile)) {
        return ['scans' => [], 'present' => [], 'last_by_uid' => [], 'rows_chrono' => []];
    }
    $lines = file($logFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    foreach ($lines as $line) {
        $parts = explode(',', $line, 5);
        if (count($parts) < 3) continue;
        $time = trim($parts[0]);
        $device = trim($parts[1]);
        $uid = normalize_uid($parts[2]);
        if ($uid === '') {
            continue;
        }
        $event = 'in';
        if (isset($parts[3]) && trim($parts[3]) !== '') {
            $ev = strtolower(trim($parts[3]));
            if ($ev === 'in' || $ev === 'out') {
                $event = $ev;
            }
        }
        $clientLocal = isset($parts[4]) ? trim($parts[4]) : '';
        $row = [
            'time' => $time,
            'device' => $device,
            'uid' => $uid,
            'event' => $event,
            'client_local' => $clientLocal,
        ];
        $rowsChrono[] = $row;
        $lastByUid[$uid] = $row;
    }
    $present = [];
    foreach ($lastByUid as $uid => $meta) {
        if ($meta['event'] === 'in') {
            $present[] = [
                'uid' => $uid,
                'since' => $meta['time'],
                'device' => $meta['device'],
            ];
        }
    }
    return [
        'scans' => array_reverse($rowsChrono),
        'present' => $present,
        'last_by_uid' => $lastByUid,
        'rows_chrono' => $rowsChrono,
    ];
}

function load_employee_names($empFile) {
    if (!file_exists($empFile)) {
        return [];
    }
    $raw = @file_get_contents($empFile);
    $data = json_decode($raw, true);
    if (!is_array($data)) {
        return [];
    }
    $out = [];
    foreach ($data as $k => $v) {
        $nk = normalize_uid($k);
        if ($nk !== '' && $v !== null && (string)$v !== '') {
            $out[$nk] = (string)$v;
        }
    }
    return $out;
}

function display_name_for_uid($uid, $namesMap) {
    $nuid = normalize_uid($uid);
    if ($nuid !== '' && isset($namesMap[$nuid]) && (string)$namesMap[$nuid] !== '') {
        return (string)$namesMap[$nuid];
    }
    $tail = strlen($nuid) > 8 ? substr($nuid, -8) : $nuid;
    return 'Badge ' . $tail;
}

function format_hm_from_seconds($seconds) {
    $seconds = max(0, (int)$seconds);
    $h = intdiv($seconds, 3600);
    $m = intdiv($seconds % 3600, 60);
    return sprintf('%d:%02d', $h, $m);
}

function daypart_key_for_ts($ts) {
    $h = (int)date('G', $ts);
    if ($h >= 5 && $h < 12) {
        return 'morning';
    }
    if ($h >= 12 && $h < 14) {
        return 'lunch';
    }
    if ($h >= 14 && $h < 17) {
        return 'afternoon';
    }
    if ($h >= 17 && $h < 22) {
        return 'evening';
    }
    return 'night';
}

/** Parse scan log timestamps (server format + ISO + strtotime). */
function parse_scan_log_timestamp($timeStr) {
    $s = trim((string)$timeStr);
    if ($s === '') {
        return false;
    }
    $formats = ['Y-m-d H:i:s', 'Y-m-d\TH:i:s', 'd.m.Y H:i:s'];
    foreach ($formats as $fmt) {
        $dt = DateTimeImmutable::createFromFormat($fmt, $s);
        if ($dt instanceof DateTimeImmutable) {
            return $dt->getTimestamp();
        }
    }
    // "Y-m-d H:i:s" or ISO prefix with trailing noise (e.g. microseconds)
    if (preg_match('/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/', $s, $m)) {
        $dt = DateTimeImmutable::createFromFormat('Y-m-d H:i:s', $m[1] . ' ' . $m[2]);
        if ($dt instanceof DateTimeImmutable) {
            return $dt->getTimestamp();
        }
    }
    $t = strtotime($s);
    return $t === false ? false : $t;
}

function worked_seconds_on_local_day($rowsChrono, $uid, $dayYmd, $nowTs) {
    $nuid = normalize_uid($uid);
    if ($nuid === '') {
        return 0;
    }
    $dayStart = strtotime($dayYmd . ' 00:00:00');
    if ($dayStart === false) {
        return 0;
    }
    $dayEnd = $dayStart + 86400;
    $uidRows = [];
    foreach ($rowsChrono as $r) {
        if (normalize_uid($r['uid']) === $nuid) {
            $uidRows[] = $r;
        }
    }
    $openIn = null;
    $total = 0;
    foreach ($uidRows as $r) {
        $t = parse_scan_log_timestamp($r['time']);
        if ($t === false) {
            continue;
        }
        if ($r['event'] === 'in') {
            // New IN while already IN: close previous interval at this IN (forgot checkout / double punch).
            if ($openIn !== null && $t > $openIn) {
                $segStart = max($openIn, $dayStart);
                $segEnd = min($t, $dayEnd);
                if ($segEnd > $segStart) {
                    $total += ($segEnd - $segStart);
                }
            }
            $openIn = $t;
        } else {
            if ($openIn !== null) {
                $segStart = max($openIn, $dayStart);
                $segEnd = min($t, $dayEnd);
                if ($segEnd > $segStart) {
                    $total += ($segEnd - $segStart);
                }
                $openIn = null;
            }
        }
    }
    if ($openIn !== null) {
        $segStart = max($openIn, $dayStart);
        $segEnd = min($nowTs, $dayEnd);
        if ($segEnd > $segStart) {
            $total += ($segEnd - $segStart);
        }
    }
    return $total;
}

function build_terminal_greeting_word($ts) {
    $dp = daypart_key_for_ts($ts);
    if ($dp === 'morning') {
        return 'Good morning';
    }
    if ($dp === 'lunch') {
        return 'Hi';
    }
    if ($dp === 'afternoon') {
        return 'Good afternoon';
    }
    if ($dp === 'evening') {
        return 'Good evening';
    }
    return 'Hello';
}

function build_terminal_message_scan($namesMap, $uid, $event, $serverTime) {
    $name = display_name_for_uid($uid, $namesMap);
    $ts = strtotime($serverTime) ?: time();
    $greet = build_terminal_greeting_word($ts);
    $isIn = ($event === 'in');
    $dp = daypart_key_for_ts($ts);

    if ($isIn) {
        if ($dp === 'lunch') {
            return "{$greet}, {$name} — check-in recorded. Have a nice lunch later!";
        }
        return "{$greet}, {$name} — check-in recorded. Have a productive day!";
    }
    if ($dp === 'lunch') {
        return "{$greet}, {$name} — check-out recorded. Enjoy your break!";
    }
    if ($dp === 'evening' || $dp === 'night') {
        return "{$greet}, {$name} — check-out recorded. Rest well!";
    }
    return "{$greet}, {$name} — check-out recorded. See you soon!";
}

function build_terminal_message_query($namesMap, $uid, $lastByUid, $workedHm, $queryKind) {
    $name = display_name_for_uid($uid, $namesMap);
    $greet = build_terminal_greeting_word(time());
    $nuid = normalize_uid($uid);

    if ($nuid === '' || !isset($lastByUid[$nuid])) {
        $stateLine = 'No punches recorded for this badge yet.';
    } elseif ($lastByUid[$nuid]['event'] === 'in') {
        $since = $lastByUid[$nuid]['time'];
        $stateLine = "You are clocked in since {$since}.";
    } else {
        $since = $lastByUid[$nuid]['time'];
        $stateLine = "You are clocked out (last event was check-out at {$since}).";
    }

    $timeLine = "Worked so far today: {$workedHm} (running total).";
    $base = "{$greet}, {$name}. {$stateLine} {$timeLine}";

    if ($queryKind === 'flextime') {
        return $base . ' Flextime details will appear here when HR is connected.';
    }
    if ($queryKind === 'holiday') {
        return $base . ' Holiday balance will appear here when HR is connected.';
    }
    return $base;
}

function build_today_summary_list($logFile, $namesMap, $parsed = null) {
    if ($parsed === null) {
        $parsed = parse_scans_log_file($logFile);
    }
    $today = date('Y-m-d');
    $now = time();
    $uidSet = [];
    foreach (array_keys($parsed['last_by_uid']) as $u) {
        $uidSet[$u] = true;
    }
    foreach ($parsed['rows_chrono'] as $r) {
        if (strpos($r['time'], $today) === 0) {
            $uidSet[$r['uid']] = true;
        }
    }
    $out = [];
    foreach (array_keys($uidSet) as $uid) {
        $sec = worked_seconds_on_local_day($parsed['rows_chrono'], $uid, $today, $now);
        $st = isset($parsed['last_by_uid'][$uid]) ? $parsed['last_by_uid'][$uid]['event'] : '';
        $since = isset($parsed['last_by_uid'][$uid]) ? $parsed['last_by_uid'][$uid]['time'] : '';
        $out[] = [
            'uid' => $uid,
            'display_name' => display_name_for_uid($uid, $namesMap),
            'state' => $st,
            'since' => $since,
            'worked_seconds_today' => $sec,
            'worked_today_hm' => format_hm_from_seconds($sec),
        ];
    }
    usort($out, function ($a, $b) {
        return strcmp($a['display_name'], $b['display_name']);
    });
    return $out;
}

function next_punch_event_for_uid($lastByUid, $uid) {
    $n = normalize_uid($uid);
    if ($n === '' || !isset($lastByUid[$n])) {
        return 'in';
    }
    return ($lastByUid[$n]['event'] === 'in') ? 'out' : 'in';
}

/** Server-local time-of-day messaging for the dashboard */
function daypart_banner() {
    $h = (int)date('G');
    if ($h >= 5 && $h < 12) {
        return [
            'title' => 'Good morning',
            'message' => 'A fresh start — remember to clock in when you begin working.',
        ];
    }
    if ($h >= 12 && $h < 14) {
        return [
            'title' => 'Lunch & break time',
            'message' => 'Recharge properly; a short break helps the whole afternoon.',
        ];
    }
    if ($h >= 14 && $h < 17) {
        return [
            'title' => 'Good afternoon',
            'message' => 'Steady progress — you are past the midday dip.',
        ];
    }
    if ($h >= 17 && $h < 22) {
        return [
            'title' => 'Good evening',
            'message' => 'Wrapping up? Do not forget to clock out before you leave.',
        ];
    }
    return [
        'title' => 'Hello, night owl',
        'message' => 'Late hours — stay safe and keep your punches accurate.',
    ];
}

function extract_bearer_token() {
    $authHeader = "";
    if (isset($_SERVER['HTTP_AUTHORIZATION'])) {
        $authHeader = $_SERVER['HTTP_AUTHORIZATION'];
    } elseif (isset($_SERVER['HTTP_X_AUTHORIZATION'])) {
        $authHeader = $_SERVER['HTTP_X_AUTHORIZATION'];
    } elseif (function_exists('getallheaders')) {
        $headers = getallheaders();
        $authHeader = $headers['Authorization']
            ?? ($headers['authorization']
            ?? ($headers['X-Authorization']
            ?? ($headers['x-authorization'] ?? '')));
    }
    if (!preg_match('/Bearer\s(\S+)/', $authHeader, $matches)) {
        return null;
    }
    return $matches[1];
}

function touch_device_last_seen($devicesFile, $deviceId, $when) {
    $devices = load_devices($devicesFile);
    if (!isset($devices[$deviceId])) {
        $devices[$deviceId] = [
            'status' => 'approved',
            'first_seen' => $when,
            'last_seen' => $when,
        ];
    } else {
        $devices[$deviceId]['last_seen'] = $when;
        $devices[$deviceId]['status'] = 'approved';
    }
    save_devices($devicesFile, $devices);
}

// Set standard JSON header (dashboard will override this)

header("Content-Type: application/json; charset=utf-8");

// Determine requested action
$action = isset($_GET['action']) ? $_GET['action'] : 'dashboard';

// Helper: Base64URL Encoding for JWT
function base64UrlEncode($text) {
    return str_replace(['+', '/', '='], ['-', '_', ''], base64_encode($text));
}

// Helper: Base64URL Decoding
function base64UrlDecode($text) {
    return base64_decode(str_replace(['-', '_'], ['+', '/'], $text));
}

// Helper: Create HS256 JWT Token
function generate_jwt($dev_id, $secret_key) {
    $header = json_encode(['typ' => 'JWT', 'alg' => 'HS256']);
    $payload = json_encode([
        'device_id' => $dev_id,
        'exp' => time() + 3600, // Expires in 1 hour
        'iat' => time()
    ]);
    
    $baseHeader = base64UrlEncode($header);
    $basePayload = base64UrlEncode($payload);
    
    $signature = hash_hmac('sha256', $baseHeader . "." . $basePayload, $secret_key, true);
    $baseSignature = base64UrlEncode($signature);
    
    return $baseHeader . "." . $basePayload . "." . $baseSignature;
}

// Helper: Verify HS256 JWT Token
function verify_jwt($jwt, $secret_key) {
    $parts = explode('.', $jwt);
    if (count($parts) !== 3) return false;
    
    list($baseHeader, $basePayload, $baseSignature) = $parts;
    
    $reSignature = base64UrlEncode(hash_hmac('sha256', $baseHeader . "." . $basePayload, $secret_key, true));
    
    // Check Signature
    if ($baseSignature !== $reSignature) return false;
    
    // Check Expiration
    $payload = json_decode(base64UrlDecode($basePayload), true);
    if (!$payload || !isset($payload['exp'])) return false;
    
    if (time() > $payload['exp']) return false; // Expired
    
    return $payload;
}

// Parse raw incoming JSON payload
$input = json_decode(file_get_contents('php://input'), true) ?? [];

// ------------------ API ROUTES ------------------

// 1. AUTHENTICATION ENDPOINT
if ($action === 'login') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        http_response_code(405);
        echo json_encode(["error" => "Method Not Allowed"]);
        exit;
    }
    
    $req_id = $input['device_id'] ?? '';
    $req_secret = $input['secret'] ?? '';
    
    // 1. Global Key Check First
    if ($req_secret !== $GLOBAL_SHARED_SECRET || empty($req_id)) {
        http_response_code(401);
        echo json_encode(["error" => "Unauthorized: Invalid credentials"]);
        exit;
    }
    
    // 2. Device registry: shared secret already validated — register or refresh and issue JWT
    $devices = load_devices($DEVICES_FILE);
    $now = date('Y-m-d H:i:s');
    if (!isset($devices[$req_id])) {
        $devices[$req_id] = [
            "status" => "approved",
            "first_seen" => $now,
            "last_seen" => $now
        ];
    } else {
        $devices[$req_id]['last_seen'] = $now;
        $devices[$req_id]['status'] = 'approved';
    }
    save_devices($DEVICES_FILE, $devices);
    
    $token = generate_jwt($req_id, $JWT_SECRET_KEY);
    echo json_encode([
        "success" => true,
        "token" => $token,
        "expires_in" => 3600
    ]);

    exit;
}

// 2. SCAN LOGGING ENDPOINT (check-in / check-out based on punch history)
if ($action === 'scan') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        http_response_code(405);
        exit;
    }

    $jwt = extract_bearer_token();
    if (!$jwt) {
        http_response_code(401);
        echo json_encode(["error" => "Unauthorized: Missing authentication token"]);
        exit;
    }

    $decoded_token = verify_jwt($jwt, $JWT_SECRET_KEY);
    if (!$decoded_token) {
        http_response_code(401);
        echo json_encode(["error" => "Unauthorized: Invalid or expired token"]);
        exit;
    }

    $uid = normalize_uid($input['uid'] ?? '');
    if ($uid === '') {
        http_response_code(400);
        echo json_encode(["error" => "Bad Request: 'uid' field required"]);
        exit;
    }

    $parsed = parse_scans_log_file($LOG_FILE);
    $nextEvent = next_punch_event_for_uid($parsed['last_by_uid'], $uid);

    $clientLocalRaw = isset($input['client_local_time']) ? trim((string)$input['client_local_time']) : '';
    $clientLocalSafe = $clientLocalRaw !== ''
        ? preg_replace('/[\r\n,]/', ' ', substr($clientLocalRaw, 0, 64))
        : '';

    $serverTime = date('Y-m-d H:i:s');
    $deviceId = $decoded_token['device_id'];

    $logLine = $serverTime . "," . $deviceId . "," . $uid . "," . $nextEvent;
    if ($clientLocalSafe !== '') {
        $logLine .= "," . $clientLocalSafe;
    }
    file_put_contents($LOG_FILE, $logLine . PHP_EOL, FILE_APPEND | LOCK_EX);

    touch_device_last_seen($DEVICES_FILE, $deviceId, $serverTime);

    $parsedFresh = parse_scans_log_file($LOG_FILE);
    $namesMap = load_employee_names($EMPLOYEES_FILE);
    $todayYmd = date('Y-m-d');
    $workedSec = worked_seconds_on_local_day($parsedFresh['rows_chrono'], $uid, $todayYmd, time());
    $terminalMessage = build_terminal_message_scan($namesMap, $uid, $nextEvent, $serverTime);

    http_response_code(201);
    echo json_encode([
        'success' => true,
        'message' => 'Punch saved',
        'event' => $nextEvent,
        'uid' => $uid,
        'display_name' => display_name_for_uid($uid, $namesMap),
        'server_time' => $serverTime,
        'client_local_time' => $clientLocalSafe,
        'terminal_message' => $terminalMessage,
        'worked_seconds_today' => $workedSec,
        'worked_today_hm' => format_hm_from_seconds($workedSec),
        'present' => $parsedFresh['present'],
        'present_count' => count($parsedFresh['present']),
    ]);
    exit;
}

// 2b. HEARTBEAT — updates device last_seen so the dashboard shows a live terminal
if ($action === 'heartbeat') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        http_response_code(405);
        echo json_encode(["error" => "Method Not Allowed"]);
        exit;
    }

    $jwt = extract_bearer_token();
    if (!$jwt) {
        http_response_code(401);
        echo json_encode(["error" => "Unauthorized: Missing authentication token"]);
        exit;
    }

    $decoded_token = verify_jwt($jwt, $JWT_SECRET_KEY);
    if (!$decoded_token) {
        http_response_code(401);
        echo json_encode(["error" => "Unauthorized: Invalid or expired token"]);
        exit;
    }

    $now = date('Y-m-d H:i:s');
    $deviceId = $decoded_token['device_id'] ?? '';
    if ($deviceId !== '') {
        touch_device_last_seen($DEVICES_FILE, $deviceId, $now);
    }

    echo json_encode([
        'success' => true,
        'last_seen' => $now,
        'device_id' => $deviceId,
    ]);
    exit;
}

// 2c. READ-ONLY status / info (does not write a punch)
if ($action === 'query_punch_status') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        http_response_code(405);
        echo json_encode(["error" => "Method Not Allowed"]);
        exit;
    }

    $jwt = extract_bearer_token();
    if (!$jwt) {
        http_response_code(401);
        echo json_encode(["error" => "Unauthorized: Missing authentication token"]);
        exit;
    }

    $decoded_token = verify_jwt($jwt, $JWT_SECRET_KEY);
    if (!$decoded_token) {
        http_response_code(401);
        echo json_encode(["error" => "Unauthorized: Invalid or expired token"]);
        exit;
    }

    $uid = normalize_uid($input['uid'] ?? '');
    if ($uid === '') {
        http_response_code(400);
        echo json_encode(["error" => "Bad Request: 'uid' field required"]);
        exit;
    }

    $kind = strtolower(trim((string)($input['query_kind'] ?? 'status')));
    if (!in_array($kind, ['status', 'flextime', 'holiday'], true)) {
        $kind = 'status';
    }

    $parsed = parse_scans_log_file($LOG_FILE);
    $namesMap = load_employee_names($EMPLOYEES_FILE);
    $todayYmd = date('Y-m-d');
    $workedSec = worked_seconds_on_local_day($parsed['rows_chrono'], $uid, $todayYmd, time());
    $workedHm = format_hm_from_seconds($workedSec);
    $terminalMessage = build_terminal_message_query($namesMap, $uid, $parsed['last_by_uid'], $workedHm, $kind);

    echo json_encode([
        'success' => true,
        'query_kind' => $kind,
        'uid' => $uid,
        'display_name' => display_name_for_uid($uid, $namesMap),
        'state' => isset($parsed['last_by_uid'][$uid]) ? $parsed['last_by_uid'][$uid]['event'] : '',
        'since' => isset($parsed['last_by_uid'][$uid]) ? $parsed['last_by_uid'][$uid]['time'] : '',
        'worked_seconds_today' => $workedSec,
        'worked_today_hm' => $workedHm,
        'terminal_message' => $terminalMessage,
    ]);
    exit;
}
if ($action === 'get_scans') {
    $parsed = parse_scans_log_file($LOG_FILE);
    $devices = load_devices($DEVICES_FILE);
    $namesMap = load_employee_names($EMPLOYEES_FILE);
    echo json_encode([
        "scans" => $parsed['scans'],
        "devices" => $devices,
        "present" => $parsed['present'],
        "present_count" => count($parsed['present']),
        "today_summary" => build_today_summary_list($LOG_FILE, $namesMap, $parsed),
        "server_time" => time(),
        "daypart" => daypart_banner(),
    ]);
    exit;
}

// 4. ADMINISTRATIVE DEVICE MANAGEMENT ROUTES
if ($action === 'delete_device') {
    $target = trim(strip_tags($_GET['id'] ?? ''));
    if (!empty($target)) {
        $devices = load_devices($DEVICES_FILE);
        if (isset($devices[$target])) {
            unset($devices[$target]); // Wipe completely from pool
            save_devices($DEVICES_FILE, $devices);
        }
    }
    header("Location: ?action=dashboard");
    exit;
}

// 5. LOG CLEAR — wipes punch log only (worked-time stats are derived from this file).
// Optional employees.json (display names) is NOT removed.
if ($action === 'clear') {
    if (file_exists($LOG_FILE)) {
        file_put_contents($LOG_FILE, "");
    }

    if (isset($_GET['ajax']) || isset($_SERVER['HTTP_X_REQUESTED_WITH'])) {
        echo json_encode([
            "success" => true,
            "message" => "Punch log cleared. Today’s worked-time totals will reset to zero until new punches arrive.",
        ]);
    } else {
        header("Location: ?action=dashboard");
    }
    exit;
}

// 5. HTML WEB DASHBOARD (DEFAULT)
if ($action === 'dashboard') {

    header("Content-Type: text/html; charset=utf-8");

    $parsed = parse_scans_log_file($LOG_FILE);
    $scans = $parsed['scans'];
    $presentList = $parsed['present'];
    $daypart = daypart_banner();

    $registeredDevices = load_devices($DEVICES_FILE);
    $namesMap = load_employee_names($EMPLOYEES_FILE);
    $todaySummary = build_today_summary_list($LOG_FILE, $namesMap, $parsed);
    ?>
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>RFID Access Portal | Monitor</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg: #0a0f1d;
                --card-bg: rgba(255, 255, 255, 0.03);
                --border: rgba(255, 255, 255, 0.07);
                --accent: #4f46e5;
                --text: #f8fafc;
                --subtext: #94a3b8;
                --success: #10b981;
            }

            * { box-sizing: border-box; margin: 0; padding: 0; }
            body {
                font-family: 'Outfit', sans-serif;
                background-color: var(--bg);
                color: var(--text);
                line-height: 1.6;
                padding: 40px 20px;
                background-image: radial-gradient(circle at top right, rgba(79, 70, 229, 0.1), transparent 40%);
                min-height: 100vh;
            }

            .container {
                max-width: 900px;
                margin: 0 auto;
            }

            header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 40px;
                border-bottom: 1px solid var(--border);
                padding-bottom: 20px;
            }

            h1 {
                font-weight: 700;
                font-size: 2rem;
                letter-spacing: -0.5px;
                display: flex;
                align-items: center;
                gap: 12px;
            }

            h1 span { color: var(--accent); }

            .status-indicator {
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 0.9rem;
                background: rgba(16, 185, 129, 0.1);
                padding: 6px 14px;
                border-radius: 20px;
                color: var(--success);
                font-weight: 600;
            }
            .pulse {
                width: 8px;
                height: 8px;
                background: var(--success);
                border-radius: 50%;
                animation: blink 1.5s infinite ease-in-out;
            }

            @keyframes blink {
                0%, 100% { opacity: 0.3; }
                50% { opacity: 1; }
            }

            .btn {
                background: var(--accent);
                color: white;
                text-decoration: none;
                padding: 8px 16px;
                border-radius: 8px;
                font-size: 0.9rem;
                font-weight: 600;
                transition: all 0.2s ease;
            }
            .btn:hover {
                opacity: 0.9;
                transform: translateY(-1px);
            }

            .btn-danger {
                background: rgba(239, 68, 68, 0.1) !important;
                color: #ef4444 !important;
                border: 1px solid rgba(239, 68, 68, 0.2);
            }
            .btn-danger:hover {
                background: #ef4444 !important;
                color: white !important;
                border-color: transparent;
            }

            /* Status Badges for Device Management */
            .badge {
                padding: 4px 10px;
                border-radius: 20px;
                font-size: 0.72rem;
                font-weight: 700;
                letter-spacing: 0.5px;
                display: inline-block;
            }
            .badge-approved {
                background: rgba(16, 185, 129, 0.1);
                color: #10b981;
                border: 1px solid rgba(16, 185, 129, 0.2);
            }
            .badge-pending {
                background: rgba(245, 158, 11, 0.1);
                color: #f59e0b;
                border: 1px solid rgba(245, 158, 11, 0.2);
            }

            .stats {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 20px;
                margin-bottom: 32px;
            }

            .daypart-banner {
                background: linear-gradient(135deg, rgba(79, 70, 229, 0.15), rgba(16, 185, 129, 0.08));
                border: 1px solid var(--border);
                border-radius: 16px;
                padding: 28px 32px;
                margin-bottom: 28px;
            }
            .daypart-banner h2 {
                font-size: 1.5rem;
                font-weight: 700;
                margin-bottom: 8px;
            }
            .daypart-banner p {
                color: var(--subtext);
                font-size: 1rem;
                max-width: 42rem;
            }

            .badge-in {
                background: rgba(16, 185, 129, 0.15);
                color: #34d399;
                border: 1px solid rgba(16, 185, 129, 0.3);
            }
            .badge-out {
                background: rgba(239, 68, 68, 0.12);
                color: #f87171;
                border: 1px solid rgba(239, 68, 68, 0.25);
            }

            .stat-card {
                background: var(--card-bg);
                border: 1px solid var(--border);
                backdrop-filter: blur(10px);
                padding: 24px;
                border-radius: 16px;
            }

            .stat-card h3 {
                font-size: 0.85rem;
                text-transform: uppercase;
                letter-spacing: 1px;
                color: var(--subtext);
                margin-bottom: 8px;
            }

            .stat-card .val {
                font-size: 2rem;
                font-weight: 700;
            }

            .table-container {
                background: var(--card-bg);
                border: 1px solid var(--border);
                border-radius: 16px;
                overflow: hidden;
            }

            table {
                width: 100%;
                border-collapse: collapse;
                text-align: left;
            }

            th {
                background: rgba(255, 255, 255, 0.02);
                color: var(--subtext);
                font-weight: 600;
                text-transform: uppercase;
                font-size: 0.75rem;
                letter-spacing: 1px;
                padding: 16px 24px;
                border-bottom: 1px solid var(--border);
            }

            td {
                padding: 16px 24px;
                border-bottom: 1px solid var(--border);
            }
            
            tr:last-child td { border-bottom: none; }

            tr:hover td {
                background: rgba(255,255,255, 0.01);
            }

            .uid {
                font-family: 'Space Mono', monospace;
                background: rgba(255, 255, 255, 0.05);
                padding: 4px 10px;
                border-radius: 6px;
                font-size: 0.9rem;
                color: #38bdf8;
                font-weight: 700;
            }

            .empty-state {
                text-align: center;
                padding: 60px 20px;
                color: var(--subtext);
            }

            @keyframes flash-green {
                0% { background-color: rgba(16, 185, 129, 0.2); }
                100% { background-color: transparent; }
            }
            .new-row td {
                animation: flash-green 2.5s ease-out;
            }

            @media(max-width: 900px) {
                .stats { grid-template-columns: 1fr; }
            }
            @media(max-width: 600px) {
                table { font-size: 0.9rem; }
                th, td { padding: 12px 16px; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>RFID <span>Portal</span></h1>
                <div style="display: flex; align-items: center; gap: 12px;">
                    <a href="?action=clear" 
                       onclick="return confirm('⚠️ Clear the entire punch log? All check-in/out history and computed “worked today” totals will reset to zero. Employee name file (employees.json) is kept.')" 
                       class="btn btn-danger">🗑️ Clear punch log</a>
                    <a href="?action=dashboard" class="btn">🔄 Refresh</a>
                    <div class="status-indicator">
                        <div class="pulse"></div> ONLINE
                    </div>
                </div>
            </header>

            <div class="daypart-banner">
                <h2 id="daypart-title"><?= htmlspecialchars($daypart['title']) ?></h2>
                <p id="daypart-message"><?= htmlspecialchars($daypart['message']) ?></p>
            </div>

            <div class="stats">
                <div class="stat-card">
                    <h3>Total punches</h3>
                    <div class="val" id="stat-total"><?= count($scans); ?></div>
                </div>
                <div class="stat-card">
                    <h3>Clocked in now</h3>
                    <div class="val" id="stat-present"><?= count($presentList); ?></div>
                </div>
                <div class="stat-card">
                    <h3>Last punch</h3>
                    <div class="val" id="stat-last-time" style="font-size: 1.2rem; margin-top: 10px;">
                        <?= !empty($scans) ? htmlspecialchars($scans[0]['time']) : 'No punches yet' ?>
                    </div>
                </div>
            </div>

            <h2 style="font-size: 1.2rem; margin-bottom: 15px; color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 10px;">
                👤 Currently clocked in
            </h2>
            <div class="table-container" id="present-wrapper" style="margin-bottom: 32px;">
                <?php if (empty($presentList)): ?>
                    <div class="empty-state" id="present-empty" style="padding: 32px 20px;">
                        <p>Nobody is currently clocked in (last punch per badge must be <strong>CHECK IN</strong>).</p>
                    </div>
                <?php else: ?>
                    <table id="present-table">
                        <thead>
                            <tr>
                                <th>Badge UID</th>
                                <th>Since</th>
                                <th>Terminal</th>
                            </tr>
                        </thead>
                        <tbody id="present-list">
                            <?php foreach ($presentList as $p): ?>
                                <tr>
                                    <td><span class="uid"><?= htmlspecialchars($p['uid']); ?></span></td>
                                    <td><?= htmlspecialchars($p['since']); ?></td>
                                    <td><?= htmlspecialchars($p['device']); ?></td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php endif; ?>
            </div>

            <h2 style="font-size: 1.2rem; margin-bottom: 15px; color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 10px;">
                ⏱️ Worked today (server date, running clock if still checked in)
            </h2>
            <div class="table-container" id="today-wrapper" style="margin-bottom: 32px;">
                <?php if (empty($todaySummary)): ?>
                    <div class="empty-state" style="padding: 28px 20px;">
                        <p>No badges seen in today’s log yet.</p>
                    </div>
                <?php else: ?>
                    <table id="today-table">
                        <thead>
                            <tr>
                                <th>Employee</th>
                                <th>Badge UID</th>
                                <th>Status</th>
                                <th>Worked today</th>
                            </tr>
                        </thead>
                        <tbody id="today-list">
                            <?php foreach ($todaySummary as $row): 
                                $st = $row['state'];
                                if ($st === 'in') {
                                    $stHtml = '<span class="badge badge-in">IN</span>';
                                } elseif ($st === 'out') {
                                    $stHtml = '<span class="badge badge-out">OUT</span>';
                                } else {
                                    $stHtml = '<span style="color:var(--subtext);">—</span>';
                                }
                            ?>
                                <tr>
                                    <td><?= htmlspecialchars($row['display_name']) ?></td>
                                    <td><span class="uid"><?= htmlspecialchars($row['uid']) ?></span></td>
                                    <td><?= $stHtml ?></td>
                                    <td><strong><?= htmlspecialchars($row['worked_today_hm']) ?></strong> <span style="color:var(--subtext);font-size:0.85rem;">(h:mm)</span></td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php endif; ?>
            </div>

            <h2 style="font-size: 1.2rem; margin-bottom: 15px; color: var(--text); display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-top: 8px;">
                📡 Registered devices
            </h2>
            <div class="table-container" id="device-wrapper" style="margin-bottom: 40px;">
                <?php if (empty($registeredDevices)): ?>
                    <div class="empty-state" id="device-empty" style="padding: 40px 20px;">
                        <h3 style="font-size: 1rem;">No hardware devices seen yet</h3>
                        <p style="margin-top: 6px; font-size: 0.9rem; color: var(--subtext);">Run the Pi client (or mock) with the correct shared secret; devices appear here after their first login.</p>
                    </div>
                <?php else: ?>
                    <table>
                        <thead>
                            <tr>
                                <th>Device Name</th>
                                <th>Connection</th>
                                <th>Last activity</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="device-list">
                            <?php foreach ($registeredDevices as $name => $data): 
                                $lastSeenUnix = @strtotime($data['last_seen'] ?? '');
                                $secondsAgo = $lastSeenUnix ? (time() - $lastSeenUnix) : 999999;
                                $isLive = ($secondsAgo <= 600);
                                $tokenLikelyValid = ($secondsAgo <= 3600);
                            ?>
                                <tr>
                                    <td><strong style="font-size: 0.95rem;"><?= htmlspecialchars($name) ?></strong></td>
                                    <td>
                                        <?php if ($isLive): ?>
                                            <span style="color: var(--success); font-weight: 600; font-size: 0.85rem; display: flex; align-items: center; gap: 6px;">
                                                <span class="pulse" style="width: 6px; height: 6px;"></span> TERMINAL LIVE
                                            </span>
                                        <?php elseif ($tokenLikelyValid): ?>
                                            <span style="color: #fbbf24; font-size: 0.85rem;">RECENT (token may still be valid)</span>
                                        <?php else: ?>
                                            <span style="color: var(--subtext); font-size: 0.85rem;">IDLE / OFFLINE</span>
                                        <?php endif; ?>
                                    </td>
                                    <td>
                                        <span style="font-size: 0.85rem; color: var(--subtext);"><?= htmlspecialchars($data['last_seen'] ?? 'N/A') ?></span>
                                    </td>
                                    <td>
                                        <a href="?action=delete_device&id=<?= urlencode($name) ?>" 
                                           onclick="return confirm('⚠️ Delete Device Log permanently?')" 
                                           class="btn" 
                                           style="padding: 4px 8px; font-size: 0.75rem; background: rgba(239, 68, 68, 0.15); color: #ef4444; border-radius: 6px;">🗑️ Remove</a>
                                    </td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php endif; ?>
            </div>

            <h2 style="font-size: 1.2rem; margin-bottom: 15px; color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 10px;">
                📑 Punch log (newest first)
            </h2>

            <div class="table-container" id="table-wrapper">
                <?php if (empty($scans)): ?>
                    <div class="empty-state" id="empty-message">
                        <h3>No punches yet</h3>
                        <p style="margin-top: 8px;">Use the terminal to clock in or out.</p>
                    </div>
                <?php else: ?>
                    <table id="scan-table">
                        <thead>
                            <tr>
                                <th>Timestamp</th>
                                <th>Device</th>
                                <th>Badge UID</th>
                                <th>Punch</th>
                            </tr>
                        </thead>
                        <tbody id="scan-list">
                            <?php foreach ($scans as $scan): 
                                $ev = isset($scan['event']) ? strtolower($scan['event']) : 'in';
                                $badgeClass = ($ev === 'out') ? 'badge-out' : 'badge-in';
                                $evLabel = ($ev === 'out') ? 'CHECK OUT' : 'CHECK IN';
                            ?>
                                <tr>
                                    <td><?= htmlspecialchars($scan['time']); ?></td>
                                    <td><?= htmlspecialchars($scan['device']); ?></td>
                                    <td><span class="uid"><?= htmlspecialchars($scan['uid']); ?></span></td>
                                    <td><span class="badge <?= $badgeClass ?>"><?= htmlspecialchars($evLabel) ?></span></td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php endif; ?>
            </div>
        </div>

        <script>
            const POLL_RATE_MS = 2000; 
            let cachedLatestTimestamp = "<?= !empty($scans) ? $scans[0]['time'] : '' ?>";
            let cachedTotalCount = <?= count($scans) ?>;
            let cachedDeviceHash = '<?= json_encode($registeredDevices) ?>';
            let cachedPresentHash = '<?= json_encode($presentList) ?>';
            let cachedTodayHash = '<?= json_encode($todaySummary) ?>';

            function statusBadgeCell(state) {
                const st = (state || '').toLowerCase();
                if (st === 'in') return '<span class="badge badge-in">IN</span>';
                if (st === 'out') return '<span class="badge badge-out">OUT</span>';
                return '<span style="color:var(--subtext);">—</span>';
            }

            function renderTodayTable(rows) {
                const wrap = document.getElementById('today-wrapper');
                if (!wrap) return;
                if (!rows.length) {
                    wrap.innerHTML = `<div class="empty-state" style="padding: 28px 20px;">
                        <p>No badges seen in today’s log yet.</p>
                    </div>`;
                    return;
                }
                let body = '';
                rows.forEach(r => {
                    body += `<tr>
                        <td>${safeEscape(r.display_name || '')}</td>
                        <td><span class="uid">${safeEscape(r.uid)}</span></td>
                        <td>${statusBadgeCell(r.state)}</td>
                        <td><strong>${safeEscape(r.worked_today_hm)}</strong> <span style="color:var(--subtext);font-size:0.85rem;">(h:mm)</span></td>
                    </tr>`;
                });
                wrap.innerHTML = `
                    <table id="today-table">
                        <thead>
                            <tr><th>Employee</th><th>Badge UID</th><th>Status</th><th>Worked today</th></tr>
                        </thead>
                        <tbody id="today-list">${body}</tbody>
                    </table>`;
            }

            function punchBadgeHtml(event) {
                const ev = (event || 'in').toLowerCase();
                const isOut = ev === 'out';
                const cls = isOut ? 'badge badge-out' : 'badge badge-in';
                const label = isOut ? 'CHECK OUT' : 'CHECK IN';
                return `<span class="${cls}">${label}</span>`;
            }

            function renderPresentTable(present) {
                const wrap = document.getElementById('present-wrapper');
                if (!wrap) return;
                if (!present.length) {
                    wrap.innerHTML = `<div class="empty-state" id="present-empty" style="padding: 32px 20px;">
                        <p>Nobody is currently clocked in (last punch per badge must be <strong>CHECK IN</strong>).</p>
                    </div>`;
                    return;
                }
                let body = '';
                present.forEach(p => {
                    body += `<tr>
                        <td><span class="uid">${safeEscape(p.uid)}</span></td>
                        <td>${safeEscape(p.since)}</td>
                        <td>${safeEscape(p.device)}</td>
                    </tr>`;
                });
                wrap.innerHTML = `
                    <table id="present-table">
                        <thead>
                            <tr><th>Badge UID</th><th>Since</th><th>Terminal</th></tr>
                        </thead>
                        <tbody id="present-list">${body}</tbody>
                    </table>`;
            }

            async function refreshData() {
                try {
                    const response = await fetch('?action=get_scans');
                    if (!response.ok) return;
                    
                    const payload = await response.json();
                    const scans = payload.scans || [];
                    const devices = payload.devices || {};
                    const present = payload.present || [];
                    const todaySummary = payload.today_summary || [];
                    const serverTime = payload.server_time || Math.floor(Date.now() / 1000);
                    const daypart = payload.daypart || {};

                    const dayTitle = document.getElementById('daypart-title');
                    const dayMsg = document.getElementById('daypart-message');
                    if (dayTitle && daypart.title) dayTitle.textContent = daypart.title;
                    if (dayMsg && daypart.message) dayMsg.textContent = daypart.message;

                    const statPresent = document.getElementById('stat-present');
                    if (statPresent) statPresent.textContent = String(present.length);

                    const presentHash = JSON.stringify(present);
                    if (presentHash !== cachedPresentHash) {
                        renderPresentTable(present);
                        cachedPresentHash = presentHash;
                    }

                    const todayHash = JSON.stringify(todaySummary);
                    if (todayHash !== cachedTodayHash) {
                        renderTodayTable(todaySummary);
                        cachedTodayHash = todayHash;
                    }

                    const latest = scans.length > 0 ? scans[0] : null;
                    const latestTime = latest ? latest.time : '';
                    if (latestTime !== cachedLatestTimestamp || scans.length !== cachedTotalCount) {
                        const totalLabel = document.getElementById('stat-total');
                        const lastTimeLabel = document.getElementById('stat-last-time');
                        if (totalLabel) totalLabel.textContent = scans.length;
                        if (lastTimeLabel) lastTimeLabel.textContent = latestTime || 'No punches yet';

                        if (scans.length !== cachedTotalCount && totalLabel) {
                            totalLabel.style.transition = 'color 0.2s';
                            totalLabel.style.color = '#10b981';
                            setTimeout(() => totalLabel.style.color = '', 1200);
                        }

                        const wrapper = document.getElementById('table-wrapper');
                        if (wrapper) {
                            if (!scans.length) {
                                wrapper.innerHTML = `<div class="empty-state" id="empty-message">
                                    <h3>No punches yet</h3>
                                    <p style="margin-top: 8px;">Use the terminal to clock in or out.</p>
                                </div>`;
                            } else {
                                const emptyMessage = document.getElementById('empty-message');
                                if (emptyMessage) emptyMessage.remove();
                                let tableBody = document.getElementById('scan-list');
                                if (!tableBody) {
                                    wrapper.innerHTML = `
                                        <table id="scan-table">
                                            <thead>
                                                <tr>
                                                    <th>Timestamp</th>
                                                    <th>Device</th>
                                                    <th>Badge UID</th>
                                                    <th>Punch</th>
                                                </tr>
                                            </thead>
                                            <tbody id="scan-list"></tbody>
                                        </table>`;
                                    tableBody = document.getElementById('scan-list');
                                }
                                let htmlStr = '';
                                scans.forEach(scan => {
                                    const isBrandNew = cachedLatestTimestamp && scan.time > cachedLatestTimestamp;
                                    const rowStyle = isBrandNew ? 'class="new-row"' : '';
                                    htmlStr += `
                                        <tr ${rowStyle}>
                                            <td>${safeEscape(scan.time)}</td>
                                            <td>${safeEscape(scan.device)}</td>
                                            <td><span class="uid">${safeEscape(scan.uid)}</span></td>
                                            <td>${punchBadgeHtml(scan.event)}</td>
                                        </tr>`;
                                });
                                tableBody.innerHTML = htmlStr;
                            }
                        }

                        cachedLatestTimestamp = latestTime;
                        cachedTotalCount = scans.length;
                    }

                    const currentDeviceHash = JSON.stringify(devices);
                    if (currentDeviceHash !== cachedDeviceHash) {
                        const devWrapper = document.getElementById('device-wrapper');
                        const devEmpty = document.getElementById('device-empty');
                        if (devEmpty && Object.keys(devices).length > 0) devEmpty.remove();

                        let devBody = document.getElementById('device-list');
                        if (!devBody && Object.keys(devices).length > 0 && devWrapper) {
                            devWrapper.innerHTML = `
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Device Name</th>
                                            <th>Connection</th>
                                            <th>Last activity</th>
                                            <th>Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody id="device-list"></tbody>
                                </table>`;
                            devBody = document.getElementById('device-list');
                        }

                        if (devBody) {
                            let devHtml = '';
                            Object.entries(devices).forEach(([name, data]) => {
                                const cleanTimeStr = (data.last_seen || "").replace(' ', 'T');
                                const lastSeenUnix = Math.floor(Date.parse(cleanTimeStr) / 1000) || 0;
                                const secondsAgo = serverTime - lastSeenUnix;
                                const isLive = secondsAgo <= 600;
                                const tokenWarm = secondsAgo <= 3600;
                                let conn = '';
                                if (isLive) {
                                    conn = `<span style="color: var(--success); font-weight: 600; font-size: 0.85rem; display:flex;align-items:center;gap:6px;">
                                        <span class="pulse" style="width:6px;height:6px;"></span> TERMINAL LIVE</span>`;
                                } else if (tokenWarm) {
                                    conn = `<span style="color:#fbbf24;font-size:0.85rem;">RECENT (token may still be valid)</span>`;
                                } else {
                                    conn = `<span style="color:var(--subtext);font-size:0.85rem;">IDLE / OFFLINE</span>`;
                                }
                                devHtml += `
                                    <tr>
                                        <td><strong style="font-size:0.95rem;">${safeEscape(name)}</strong></td>
                                        <td>${conn}</td>
                                        <td><span style="font-size:0.85rem;color:var(--subtext);">${safeEscape(data.last_seen || "N/A")}</span></td>
                                        <td>
                                            <a href="?action=delete_device&id=${encodeURIComponent(name)}" onclick="return confirm('⚠️ Delete Device Log permanently?')" class="btn" style="padding:4px 8px;font-size:0.75rem;background:rgba(239,68,68,0.15);color:#ef4444;border-radius:6px;">🗑️ Remove</a>
                                        </td>
                                    </tr>`;
                            });
                            devBody.innerHTML = devHtml;
                        }
                        cachedDeviceHash = currentDeviceHash;
                    }
                } catch (err) {
                    console.error('[POLL ERROR]:', err);
                }
            }

            function safeEscape(str) {
                const el = document.createElement('div');
                el.textContent = str;
                return el.innerHTML;
            }

            setInterval(refreshData, POLL_RATE_MS);
        </script>
    </body>
    </html>
    <?php
    exit;
}
