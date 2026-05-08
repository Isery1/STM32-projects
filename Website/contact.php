<?php
if ($_SERVER["REQUEST_METHOD"] == "POST") {
    // Collect and sanitize inputs
    $name = strip_tags(trim($_POST["name"]));
    $email = filter_var(trim($_POST["email"]), FILTER_SANITIZE_EMAIL);
    $subject = strip_tags(trim($_POST["subject"]));
    $message = trim($_POST["message"]);

    if (empty($name) || empty($message) || !filter_var($email, FILTER_VALIDATE_EMAIL)) {
        http_response_code(400);
        echo "Please complete the form correctly.";
        exit;
    }

    $recipient = "kontakt@framegeist.at";
    $email_subject = "Framegeist Website-Anfrage von $name" . ($subject ? ": $subject" : "");
    
    $email_content = "Name: $name\n";
    $email_content .= "E-Mail: $email\n\n";
    $email_content .= "Nachricht:\n$message\n";

    // Set headers
    $email_headers = "From: Framegeist Website <kontakt@framegeist.at>\r\n";
    $email_headers .= "Reply-To: $email\r\n";
    $email_headers .= "Content-Type: text/plain; charset=utf-8\r\n";

    if (mail($recipient, $email_subject, $email_content, $email_headers)) {
        http_response_code(200);
        echo "Success";
    } else {
        http_response_code(500);
        echo "Failed";
    }
} else {
    http_response_code(403);
    echo "Access Denied";
}
?>
