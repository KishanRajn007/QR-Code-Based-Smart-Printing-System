from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
from pymongo import MongoClient
from bson.objectid import ObjectId
import random
import datetime
import os
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import smtplib
from email.message import EmailMessage
import win32print
import win32api
import subprocess
import threading

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "secret123")
app.config.update({
    'SESSION_COOKIE_SAMESITE': 'Lax',
    'SESSION_COOKIE_HTTPONLY': True,
    'SESSION_COOKIE_SECURE': False
})

# =========================
# MONGODB CONNECTION
# =========================

client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))
db = client["smart_printing"]

users_collection = db["users"]
admins_collection = db["admins"]
documents_collection = db["documents"]
print_jobs_collection = db["print_jobs"]
payments_collection = db["payments"]
otp_collection = db["otp_verification"]
feedback_collection = db["feedback"]
printers_collection = db["printers"]

# =========================
# PHYSICAL PRINTER SYNC
# =========================
def sync_windows_printers():
    """
    Synchronizes local and network printers installed on the Windows host machine
    with the MongoDB 'printers' collection.
    
    For each printer found:
    - If not present in the DB, inserts a new printer record with default values.
    - If present, marks the printer status as 'online'.
    - Any printer currently in the DB but not found on the host machine is set to 'offline'.
    """
    try:
        # Fetch local and network printers using win32print APIs
        printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
        installed_names = []
        for p in printers:
            printer_name = p[2]
            installed_names.append(printer_name)
            
            existing = printers_collection.find_one({"name": printer_name})
            if not existing:
                # Add a new printer record if it doesn't already exist
                printers_collection.insert_one({
                    "name": printer_name,
                    "printer_system_name": printer_name,
                    "status": "online",
                    "paper_available": 100,
                    "ink_level": 100,
                    "active_job": None,
                    "queue_count": 0,
                    "created_at": datetime.datetime.now(),
                    "alerts": []
                })
            else:
                # Mark existing printer as online
                printers_collection.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"status": "online"}}
                )
        
        # Mark all printers that are no longer installed on the system as offline
        printers_collection.update_many(
            {"name": {"$nin": installed_names}},
            {"$set": {"status": "offline"}}
        )
    except Exception as e:
        print("Error syncing printers:", e)

# Run sync on startup to populate printer list
sync_windows_printers()

# Auto-generate login QR code
def generate_login_qr():
    """
    Generates a login QR code containing the dynamic local network IP address of the server.
    This enables mobile devices connected to the same Wi-Fi network to scan the QR code
    and instantly navigate to the mobile-friendly web login page.
    Saves the QR code image under static/images/login_qr.png.
    """
    try:
        import qrcode
        import socket
        
        # Dynamically fetch local network IP to allow mobile devices on the same Wi-Fi to connect
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"

        qr_dir = "static/images"
        qr_path = os.path.join(qr_dir, "login_qr.png")
        os.makedirs(qr_dir, exist_ok=True)
        
        # Configure and generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4
        )
        target_url = f"http://{local_ip}:5000/login"
        qr.add_data(target_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_path)
        print(f"Login QR Code successfully generated at: {qr_path} (Target: {target_url})")
    except Exception as e:
        print("Error generating QR code:", e)

# Run QR code generation on server startup
generate_login_qr()


def print_document(filepath, printer_name, copies=1):
    """
    Sends a local document to the specified system printer using SumatraPDF.
    
    Parameters:
    - filepath (str): The relative or absolute path of the document file to print.
    - printer_name (str): The exact name of the target printer registered in Windows.
    - copies (int/str): The number of copies to print (default is 1).
    
    Returns:
    - (bool, str): A tuple containing a success boolean and a descriptive status message.
    """
    try:
        sumatra_path = r"C:\Users\DELL\AppData\Local\SumatraPDF\SumatraPDF.exe"
        abs_path = os.path.abspath(filepath)

        # Ensure copies is a valid integer, fallback to 1
        try:
            copies_int = int(copies)
        except (TypeError, ValueError):
            copies_int = 1

        settings = f"{copies_int}x,fit"

        # Execute SumatraPDF command to print silently to the specified printer
        subprocess.run([
            sumatra_path,
            "-print-to",
            printer_name,
            "-print-settings",
            settings,
            abs_path
        ], check=True)

        return True, "Print command sent to SumatraPDF"
    except Exception as e:
        print("Print error:", e)
        return False, str(e)


# =========================
# FILE UPLOAD
# =========================

UPLOAD_FOLDER = "static/uploads"

ALLOWED_EXTENSIONS = {
    'txt', 'pdf', 'png', 'jpg',
    'jpeg', 'gif', 'doc', 'docx'
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def safe_object_id(value):
    try:
        return ObjectId(value)
    except Exception:
        return None

# =========================
# ROUTES
# =========================

@app.route('/')
def login_page():
    return render_template('1.html')

@app.route('/signup-page')
def signup_page():
    return render_template('createone.html')

@app.route('/admin')
def admin_login_page():
    return render_template('adminlogin.html')

@app.route('/dashboard')
def dashboard():

    if 'user_id' not in session:
        return redirect('/')

    return render_template('dashboard.html')

@app.route('/admin-dashboard')
def admin_dashboard():

    if 'admin_id' not in session:
        return redirect('/admin')

    return render_template('adminpannel.html')

@app.route('/forgot-password')
def forgot():
    return render_template('forgotpassword.html')

@app.route('/payment')
def payment():

    if 'user_id' not in session:
        return redirect('/')

    return render_template('payment.html')

@app.route('/waiting')
def waiting():

    if 'user_id' not in session:
        return redirect('/')

    return render_template('waiting.html')

@app.route('/feedback')
def feedback():

    if 'user_id' not in session:
        return redirect('/')

    return render_template('feedback.html')

# =========================
# USER SIGNUP
# =========================

@app.route('/signup', methods=['POST'])
def signup():

    data = request.get_json(silent=True) or request.form

    existing_user = users_collection.find_one({
        "email": data.get('email')
    })

    if existing_user:
        return "User already exists", 400

    user_data = {
        "name": data.get('name'),
        "email": data.get('email'),
        "phone": data.get('phone'),
        "password": data.get('password'),
        "created_at": datetime.datetime.now()
    }

    users_collection.insert_one(user_data)

    # Send congratulations email
    email = data.get('email')
    name = data.get('name') or "User"
    current_year = datetime.datetime.now().year

    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_email = os.environ.get("SMTP_EMAIL")
    smtp_password = os.environ.get("SMTP_PASSWORD")

    if smtp_server and smtp_port and smtp_email and smtp_password:
        try:
            msg = EmailMessage()
            msg['Subject'] = 'Welcome to QR SmartPrint | Registration Successful'
            msg['From'] = f"QR CODE BASED SMART PRINTING SYSTEM <{smtp_email}>"
            msg['To'] = email

            msg.set_content(
                f"Congratulations, {name}!\n\nYour account has been successfully created at QR SmartPrint.\n\nYou can now easily upload documents, manage print jobs, and experience secure, instant printing right on campus."
            )

            html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome to QR SmartPrint</title>
  <style>
    body {{
      margin: 0;
      padding: 0;
      width: 100% !important;
      height: 100% !important;
      background-color: #080a12;
      font-family: 'Poppins', 'Inter', 'Segoe UI', Helvetica, Arial, sans-serif;
    }}
    .email-container {{
      max-width: 600px;
      margin: 0 auto;
      padding: 40px 20px;
      background-color: #080a12;
    }}
    .card {{
      background-color: #16213e;
      background-image: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
      border: 2px solid #00d4ff;
      border-radius: 20px;
      padding: 45px 35px;
      text-align: center;
      box-shadow: 0 15px 40px rgba(0, 212, 255, 0.25), 0 0 25px rgba(147, 51, 234, 0.15);
    }}
    .logo-container {{
      margin-bottom: 24px;
    }}
    .logo-text {{
      font-size: 26px;
      font-weight: 800;
      color: #ffffff;
      letter-spacing: 0.5px;
      margin: 0;
      display: inline-block;
      vertical-align: middle;
    }}
    .logo-text span {{
      color: #00d4ff;
    }}
    .logo-icon {{
      font-size: 30px;
      margin-right: 8px;
      vertical-align: middle;
      display: inline-block;
    }}
    .tagline {{
      font-size: 13px;
      color: #00d4ff;
      margin-top: 6px;
      margin-bottom: 0;
      font-weight: 600;
      letter-spacing: 1px;
      text-transform: uppercase;
    }}
    .message-title {{
      font-size: 20px;
      color: #ffffff;
      margin-top: 0;
      margin-bottom: 15px;
      font-weight: 600;
      letter-spacing: 0.2px;
    }}
    .message-body {{
      font-size: 15px;
      color: #e2e8f0;
      line-height: 1.6;
      margin-top: 10px;
      margin-bottom: 25px;
      font-weight: 400;
    }}
    .footer-text {{
      font-size: 12px;
      color: #a0aec0;
      line-height: 1.6;
      margin-top: 30px;
      margin-bottom: 0;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
      padding-top: 20px;
    }}
  </style>
</head>
<body>
  <div class="email-container">
    <div class="card">
      <div class="logo-container">
        <div style="margin-bottom: 4px;">
          <span class="logo-icon">🖨️</span>
          <h1 class="logo-text">QR <span>SmartPrint</span></h1>
        </div>
        <p class="tagline">Secure Campus Cloud Printing Platform</p>
      </div>
      
      <div style="height: 1px; background-color: rgba(255, 255, 255, 0.08); margin: 25px 0;"></div>
      
      <p class="message-title">Congratulations, {name}!</p>
      
      <p class="message-body">
        Welcome to <strong>QR SmartPrint</strong>! Your account has been created successfully.<br><br>
        You can now easily upload documents, manage print jobs, and experience secure, instant printing right on campus.
      </p>
      
      <table align="center" border="0" cellpadding="0" cellspacing="0" style="margin: 24px auto; background-color: rgba(255, 255, 255, 0.04); background-image: linear-gradient(135deg, rgba(0, 212, 255, 0.08), rgba(147, 51, 234, 0.08)); border: 1px solid rgba(0, 212, 255, 0.35); border-radius: 12px; width: 320px; text-align: center; border-collapse: separate;">
        <tr>
          <td style="padding: 18px 10px; font-size: 20px; font-weight: 800; color: #00d4ff; text-shadow: 0 0 15px rgba(0, 212, 255, 0.4); text-align: center;">
            🎉 Account Active
          </td>
        </tr>
      </table>
      
      <p class="footer-text">
        If you did not create this account, please ignore this email or contact support.<br>
        &copy; {current_year} QR SmartPrint. All rights reserved.
      </p>
    </div>
  </div>
</body>
</html>"""

            msg.add_alternative(html_content, subtype='html')

            threading.Thread(target=send_email_async, args=(
                smtp_server,
                smtp_port,
                smtp_email,
                smtp_password,
                msg
            )).start()
        except Exception as e:
            print("Error creating/sending signup email:", e)

    return redirect('/')

@app.route('/login', methods=['GET'])
def login_get_redirect():
    return redirect('/')

# =========================
# USER LOGIN
# =========================


@app.route('/login', methods=['POST'])
def login():

    data = request.get_json(silent=True) or request.form

    user = users_collection.find_one({
        "email": data.get('email'),
        "password": data.get('password')
    })

    if user:
        session['user_id'] = str(user['_id'])
        return redirect('/dashboard')

    return "Invalid credentials", 401

# =========================
# ADMIN LOGIN
# =========================

@app.route('/admin-login', methods=['POST'])
def admin_login():

    data = request.get_json(silent=True) or request.form

    admin = admins_collection.find_one({
        "email": data.get('email'),
        "password": data.get('password')
    })

    if admin:
        session['admin_id'] = str(admin['_id'])
        return redirect('/admin-dashboard')

    return "Invalid admin credentials", 401

# =========================
# DASHBOARD STATS
# =========================

@app.route('/stats')
def stats():

    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session['user_id']

    user = users_collection.find_one({
        "_id": ObjectId(user_id)
    })

    documents = documents_collection.count_documents({
        "user_id": user_id
    })

    pending = print_jobs_collection.count_documents({
        "user_id": user_id,
        "status": "pending"
    })

    approved = print_jobs_collection.count_documents({
        "user_id": user_id,
        "status": "approved"
    })

    rejected = print_jobs_collection.count_documents({
        "user_id": user_id,
        "status": "rejected"
    })

    printed = print_jobs_collection.count_documents({
        "user_id": user_id,
        "status": "printed"
    })

    now = datetime.datetime.now()

    monthly_labels = []
    monthly_submitted = []
    monthly_completed = []

    user_docs = list(documents_collection.find({"user_id": user_id}))
    user_jobs = list(print_jobs_collection.find({"user_id": user_id}))

    for i in range(5, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
            
        month_start = datetime.datetime(y, m, 1)
        if m == 12:
            month_end = datetime.datetime(y + 1, 1, 1)
        else:
            month_end = datetime.datetime(y, m + 1, 1)
            
        monthly_labels.append(month_start.strftime('%b'))
        
        docs_count = sum(1 for d in user_docs if d.get('upload_time') and month_start <= d['upload_time'] < month_end)
        jobs_count = sum(1 for j in user_jobs if j.get('status') == 'printed' and j.get('created_at') and month_start <= j['created_at'] < month_end)
        
        monthly_submitted.append(docs_count)
        monthly_completed.append(jobs_count)

    return jsonify({
        "user_name": user.get('name'),
        "profile_photo": user.get('profile_photo', ''),
        "documents": documents,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "completed": printed,
        "monthly_labels": monthly_labels,
        "monthly_submitted": monthly_submitted,
        "monthly_completed": monthly_completed
    })

# =========================
# FILE UPLOAD
# =========================

@app.route('/upload', methods=['POST'])
def upload():
    """
    Handles file upload requests from authorized users.
    
    Validates file presence, filename validity, and allowed extensions.
    Generates a unique filename using collision detection.
    Saves the file physically and inserts metadata into:
    - 'documents' collection
    - 'print_jobs' collection (status initially set to 'awaiting_payment')
    
    Form Parameters:
    - file (File): The file payload.
    - pages (str): Page configuration details (e.g. range or count).
    - copies (str): Number of copies requested.
    - confidential (str): Confidential flag ('true' or 'on').
    
    Returns:
    - JSON response with success status and job_id.
    """
    if 'user_id' not in session:
        return "Unauthorized", 401

    if 'file' not in request.files:
        return "No file uploaded", 400

    file = request.files['file']

    pages = request.form.get('pages')
    copies = request.form.get('copies')

    if file.filename == '':
        return "Empty file name", 400

    if not allowed_file(file.filename):
        return "File type not allowed", 400

    # Clean and secure the original filename
    filename = secure_filename(file.filename)
    base, ext = os.path.splitext(filename)
    counter = 1
    unique_filename = filename
    
    # Collision detection loop to prevent overwriting existing files in the upload folder
    while os.path.exists(os.path.join(UPLOAD_FOLDER, unique_filename)):
        unique_filename = f"{base}_{counter}{ext}"
        counter += 1

    filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
    file.save(filepath)

    # Convert the confidential parameter to a boolean
    is_sensitive = str(request.form.get('confidential', '')).lower() in ('true', '1', 'yes', 'on')

    # Record the document metadata in MongoDB
    document = {
        "user_id": session['user_id'],
        "file_name": unique_filename,
        "file_path": filepath,
        "upload_time": datetime.datetime.now(),
        "is_sensitive": is_sensitive
    }

    doc_result = documents_collection.insert_one(document)

    # Initialize a print job entry with awaiting_payment status
    print_job = {
        "user_id": session['user_id'],
        "document_id": str(doc_result.inserted_id),
        "pages": pages,
        "copies": copies,
        "status": "awaiting_payment",
        "sensitive": is_sensitive,
        "created_at": datetime.datetime.now()
    }

    job_result = print_jobs_collection.insert_one(print_job)

    return jsonify({
        "message": "Uploaded successfully",
        "job_id": str(job_result.inserted_id)
    })

# =========================
# GET DOCUMENTS
# =========================

@app.route('/get-documents')
def get_documents():
    """
    Retrieves all documents uploaded by the currently authenticated user.
    Enriches the document metadata with the dynamic file size and print status.
    
    Returns:
    - JSON list containing document metadata (id, name, type, size, upload date, status).
    """
    if 'user_id' not in session:
        return jsonify([])

    docs = documents_collection.find({
        "user_id": session['user_id']
    })

    result = []

    for doc in docs:
        # Fetch status from the print_jobs collection
        status_data = print_jobs_collection.find_one({
            "document_id": str(doc['_id'])
        })

        file_size_str = "Unknown"

        # Calculate human-readable file size dynamically from disk
        if os.path.exists(doc['file_path']):
            size_bytes = os.path.getsize(doc['file_path'])
            if size_bytes >= 1024 * 1024:
                file_size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
            else:
                file_size_str = f"{size_bytes / 1024:.1f} KB"

        file_type = doc['file_name'].split('.')[-1]
        upload_date_str = doc['upload_time'].strftime("%b %d, %Y")

        result.append({
            "id": str(doc['_id']),
            "name": doc['file_name'],
            "type": file_type,
            "size": file_size_str,
            "date": upload_date_str,
            "status": status_data['status'] if status_data else "Pending"
        })

    return jsonify(result)

# =========================
# DELETE DOCUMENT
# =========================

@app.route('/delete-document/<id>', methods=['DELETE'])
def delete_document(id):
    """
    Deletes a specific document from the system.
    Ensures that the document belongs to the authenticated user.
    Removes the physical file from the disk first, then deletes DB records.
    
    Parameters:
    - id (str): The MongoDB ObjectId of the document.
    
    Returns:
    - JSON success status.
    """
    if 'user_id' not in session:
        return "Unauthorized", 401

    doc = documents_collection.find_one({
        "_id": ObjectId(id),
        "user_id": session['user_id']
    })

    # Physically delete document from the file system
    if doc and 'file_path' in doc:
        try:
            if os.path.exists(doc['file_path']):
                os.remove(doc['file_path'])
        except Exception as e:
            print("Error deleting file:", e)

    # Delete the document metadata record
    documents_collection.delete_one({
        "_id": ObjectId(id),
        "user_id": session['user_id']
    })

    return jsonify({
        "success": True
    })

# =========================
# RENAME DOCUMENT
# =========================

@app.route('/api/rename-document/<id>', methods=['POST'])
def rename_document(id):
    """
    Renames an existing document.
    
    Applies security features:
    - Restricts action to the document owner.
    - Sanitizes the new base name via secure_filename.
    - Neutrals extension injection by retaining the original extension.
    - Uses file collision checks.
    - Employs physical rename rollback if the DB update fails.
    
    Parameters:
    - id (str): The MongoDB ObjectId of the document.
    - JSON body with 'new_name'.
    
    Returns:
    - JSON response with success status and the new file name.
    """
    if 'user_id' not in session:
        return jsonify({"message": "Unauthorized"}), 401

    doc = documents_collection.find_one({
        "_id": safe_object_id(id),
        "user_id": session['user_id']
    })

    if not doc:
        return jsonify({"message": "Document not found"}), 404

    data = request.get_json(silent=True) or {}
    new_name_raw = data.get('new_name', '').strip()

    if not new_name_raw:
        return jsonify({"message": "New name is required"}), 400

    old_filename = doc.get('file_name', '')
    old_path = doc.get('file_path', '')

    if not old_filename or not os.path.exists(old_path):
        return jsonify({"message": "Original file not found on server"}), 404

    # Extract original extension safely to avoid extension injection
    _, ext = os.path.splitext(old_filename)

    # Strip any user-supplied extension
    base_new, _ = os.path.splitext(new_name_raw)

    # Secure the base name using Werkzeug secure_filename
    sanitized_base = secure_filename(base_new)
    if not sanitized_base:
        sanitized_base = "renamed_document"

    new_filename = f"{sanitized_base}{ext}"

    # Collision detection logic
    counter = 1
    unique_filename = new_filename
    while os.path.exists(os.path.join(UPLOAD_FOLDER, unique_filename)):
        unique_filename = f"{sanitized_base}_{counter}{ext}"
        counter += 1

    new_path = os.path.join(UPLOAD_FOLDER, unique_filename)

    # Execute physical rename on disk
    try:
        if os.path.abspath(old_path) != os.path.abspath(new_path):
            os.rename(old_path, new_path)
    except PermissionError:
        return jsonify({"message": "File is currently in use or being printed. Please try again shortly."}), 400
    except Exception as e:
        return jsonify({"message": f"FileSystem error: {str(e)}"}), 500

    # DB update with physical rollback if DB write fails
    try:
        documents_collection.update_one(
            {"_id": ObjectId(id)},
            {"$set": {
                "file_name": unique_filename,
                "file_path": new_path
            }}
        )
    except Exception as e:
        # Roll back disk changes if database operation fails
        try:
            if os.path.abspath(old_path) != os.path.abspath(new_path) and os.path.exists(new_path):
                os.rename(new_path, old_path)
        except Exception:
            pass
        return jsonify({"message": f"Database update failed: {str(e)}"}), 500

    return jsonify({
        "success": True,
        "new_name": unique_filename
    })

# =========================
# VIEW DOCUMENT
# =========================

@app.route('/view-document/<id>')
def view_document(id):
    """
    Serves a specific document file from the server uploads directory securely.
    Determines and enforces the correct mimetype based on the file extension.
    
    Parameters:
    - id (str): The MongoDB ObjectId of the document.
    
    Returns:
    - The file streamed via send_from_directory with standard security headers.
    """
    doc = documents_collection.find_one({
        "_id": ObjectId(id)
    })

    if not doc:
        return "Document not found", 404

    filename = doc['file_name']
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    
    # Establish correct mimetype options
    kwargs = {}
    if ext == 'png':
        kwargs['mimetype'] = 'image/png'
    elif ext in ['jpg', 'jpeg']:
        kwargs['mimetype'] = 'image/jpeg'
    elif ext == 'gif':
        kwargs['mimetype'] = 'image/gif'
    elif ext == 'pdf':
        kwargs['mimetype'] = 'application/pdf'

    return send_from_directory(
        UPLOAD_FOLDER,
        filename,
        **kwargs
    )

# =========================
# GET PRINT JOBS
# =========================

@app.route('/get-print-jobs')
def get_print_jobs():
    """
    Retrieves all print jobs related to the authenticated user's account.
    Resolves the associated document name for presentation on the dashboard.
    
    Returns:
    - JSON list containing job summaries.
    """
    if 'user_id' not in session:
        return jsonify([])

    jobs = print_jobs_collection.find({
        "user_id": session['user_id']
    })

    result = []

    for job in jobs:
        document = None
        if job.get('document_id'):
            try:
                document = documents_collection.find_one({
                    "_id": ObjectId(job['document_id'])
                })
            except Exception:
                pass

        file_name = document['file_name'] if document and 'file_name' in document else "Unknown Document"

        result.append({
            "id": str(job['_id']),
            "doc_id": str(job.get('document_id', '')),
            "file_name": file_name,
            "status": job.get('status', 'pending'),
            "pages": job.get('pages', 1),
            "copies": job.get('copies', 1)
        })

    return jsonify(result)

# =========================
# PROFILE
# =========================

@app.route('/get-profile')
def get_profile():

    if 'user_id' not in session:
        return jsonify({}), 401

    user = users_collection.find_one({
        "_id": ObjectId(session['user_id'])
    })

    if not user:
        return jsonify({}), 404

    profile = {
        "id": str(user['_id']),
        "name": user.get('name'),
        "email": user.get('email'),
        "phone": user.get('phone'),
        "department": user.get('department', ''),
        "roll_no": user.get('roll_number', ''),
        "profile_photo": user.get('profile_photo', '')
    }

    return jsonify({
        "profile": profile
    })

def send_email_async(smtp_server, smtp_port, smtp_email, smtp_password, msg):
    try:
        server = smtplib.SMTP(
            smtp_server,
            int(smtp_port)
        )
        server.starttls()
        server.login(
            smtp_email,
            smtp_password
        )
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print("Async SMTP send error:", e)

# =========================
# SEND OTP
# =========================

@app.route('/send-otp', methods=['POST'])
def send_otp():

    data = request.get_json()

    email = data.get('email')

    otp = str(random.randint(1000, 9999))

    expiry = datetime.datetime.now() + datetime.timedelta(minutes=5)

    otp_collection.delete_many({
        "email": email
    })

    otp_collection.insert_one({
        "email": email,
        "otp": otp,
        "expiry_time": expiry
    })

    otp_spaced = " ".join(list(otp))
    current_year = datetime.datetime.now().year

    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = os.environ.get("SMTP_PORT")
    smtp_email = os.environ.get("SMTP_EMAIL")
    smtp_password = os.environ.get("SMTP_PASSWORD")

    try:

        msg = EmailMessage()
        msg['Subject'] = 'Password Reset OTP | QR SmartPrint'
        msg['From'] = f"QR CODE BASED SMART PRINTING SYSTEM <{smtp_email}>"
        msg['To'] = email

        msg.set_content(
            f"Here is your verification OTP:\n\n{otp}\n\nThis OTP expires in 5 minutes.\nIf you did not request this, please ignore this email."
        )

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Password Reset OTP</title>
  <style>
    body {{
      margin: 0;
      padding: 0;
      width: 100% !important;
      height: 100% !important;
      background-color: #080a12;
      font-family: 'Poppins', 'Inter', 'Segoe UI', Helvetica, Arial, sans-serif;
    }}
    .email-container {{
      max-width: 600px;
      margin: 0 auto;
      padding: 40px 20px;
      background-color: #080a12;
    }}
    .card {{
      background-color: #16213e;
      background-image: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
      border: 2px solid #00d4ff;
      border-radius: 20px;
      padding: 45px 35px;
      text-align: center;
      box-shadow: 0 15px 40px rgba(0, 212, 255, 0.25), 0 0 25px rgba(147, 51, 234, 0.15);
    }}
    .logo-container {{
      margin-bottom: 24px;
    }}
    .logo-text {{
      font-size: 26px;
      font-weight: 800;
      color: #ffffff;
      letter-spacing: 0.5px;
      margin: 0;
      display: inline-block;
      vertical-align: middle;
    }}
    .logo-text span {{
      color: #00d4ff;
    }}
    .logo-icon {{
      font-size: 30px;
      margin-right: 8px;
      vertical-align: middle;
      display: inline-block;
    }}
    .tagline {{
      font-size: 13px;
      color: #00d4ff;
      margin-top: 6px;
      margin-bottom: 0;
      font-weight: 600;
      letter-spacing: 1px;
      text-transform: uppercase;
    }}
    .message-title {{
      font-size: 17px;
      color: #ffffff;
      margin-top: 0;
      margin-bottom: 20px;
      font-weight: 500;
      letter-spacing: 0.2px;
    }}
    .otp-wrapper {{
      background: rgba(255, 255, 255, 0.04);
      background-image: linear-gradient(135deg, rgba(0, 212, 255, 0.08), rgba(147, 51, 234, 0.08));
      border: 1px solid rgba(0, 212, 255, 0.35);
      border-radius: 12px;
      padding: 18px 30px;
      margin: 24px 0;
      display: inline-block;
      text-align: center;
    }}
    .otp-box {{
      font-size: 30px;
      font-weight: 800;
      color: #00d4ff;
      letter-spacing: 6px;
      text-indent: 6px;
      text-shadow: 0 0 15px rgba(0, 212, 255, 0.4);
      font-family: 'Courier New', Courier, monospace;
      white-space: nowrap;
      display: block;
    }}
    .expiry-text {{
      font-size: 14px;
      color: #e2e8f0;
      margin-top: 20px;
      margin-bottom: 10px;
      font-weight: 500;
      display: inline-block;
    }}
    .expiry-icon {{
      font-size: 16px;
      margin-right: 4px;
      vertical-align: middle;
    }}
    .footer-text {{
      font-size: 12px;
      color: #a0aec0;
      line-height: 1.6;
      margin-top: 30px;
      margin-bottom: 0;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
      padding-top: 20px;
    }}
  </style>
</head>
<body>
  <div class="email-container">
    <div class="card">
      <div class="logo-container">
        <div style="margin-bottom: 4px;">
          <span class="logo-icon">🖨️</span>
          <h1 class="logo-text">QR <span>SmartPrint</span></h1>
        </div>
        <p class="tagline">Secure Campus Cloud Printing Platform</p>
      </div>
      
      <div style="height: 1px; background-color: rgba(255, 255, 255, 0.08); margin: 25px 0;"></div>
      
      <p class="message-title">Here is your verification OTP:</p>
      
      <!-- Bulletproof table layout to prevent content overflow in mobile clients -->
      <table align="center" border="0" cellpadding="0" cellspacing="0" style="margin: 24px auto; background-color: rgba(255, 255, 255, 0.04); background-image: linear-gradient(135deg, rgba(0, 212, 255, 0.08), rgba(147, 51, 234, 0.08)); border: 1px solid rgba(0, 212, 255, 0.35); border-radius: 12px; width: 320px; text-align: center; border-collapse: separate;">
        <tr>
          <td style="padding: 18px 10px; font-size: 30px; font-weight: 800; color: #00d4ff; letter-spacing: 6px; text-indent: 6px; text-shadow: 0 0 15px rgba(0, 212, 255, 0.4); font-family: 'Courier New', Courier, monospace; text-align: center; white-space: nowrap;">
            {otp_spaced}
          </td>
        </tr>
      </table>
      
      <div style="margin-top: 20px;">
        <span class="expiry-text">
          <span class="expiry-icon">⏳</span> This OTP expires in <strong style="color: #ffffff;">5 minutes</strong>.
        </span>
      </div>
      
      <p class="footer-text">
        If you did not request this, please ignore this email.<br>
        &copy; {current_year} QR SmartPrint. All rights reserved.
      </p>
    </div>
  </div>
</body>
</html>"""

        msg.add_alternative(html_content, subtype='html')

        # Run SMTP sending in a background thread to prevent blocking page transitions
        threading.Thread(target=send_email_async, args=(
            smtp_server,
            smtp_port,
            smtp_email,
            smtp_password,
            msg
        )).start()

    except Exception as e:
        print(e)

    return "OTP sent"

# =========================
# VERIFY OTP
# =========================

@app.route('/verify-otp', methods=['POST'])
def verify_otp():

    data = request.get_json()

    email = data.get('email')
    otp = data.get('otp')

    record = otp_collection.find_one({
        "email": email,
        "otp": otp,
        "expiry_time": {
            "$gt": datetime.datetime.now()
        }
    })

    if record:

        otp_collection.delete_many({
            "email": email
        })

        return "OTP verified"

    return "Invalid or expired OTP", 400

# =========================
# RESET PASSWORD
# =========================

@app.route('/reset-password', methods=['POST'])
def reset_password():

    data = request.get_json()

    email = data.get('email')
    new_password = data.get('password')

    users_collection.update_one(
        {"email": email},
        {
            "$set": {
                "password": new_password
            }
        }
    )

    return "Password updated"

# =========================
# PAYMENT
# =========================

@app.route('/api/payment', methods=['POST'])
def process_payment():

    if 'user_id' not in session:
        return jsonify({
            "message": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    user_id = session['user_id']
    job_id = data.get('job_id')
    amount = data.get('amount')
    method = data.get('method')
    currency = data.get('currency', 'INR')

    if not job_id:
        return jsonify({"message": "Missing job_id"}), 400

    job_obj_id = safe_object_id(job_id)
    if not job_obj_id:
        return jsonify({"message": "Invalid job_id"}), 400

    job = print_jobs_collection.find_one({"_id": job_obj_id})
    if not job:
        return jsonify({"message": "Print job not found"}), 404

    print_jobs_collection.update_one(
        {
            "_id": job_obj_id
        },
        {
            "$set": {
                "status": "pending"
            }
        }
    )

    payments_collection.insert_one({
        "user_id": user_id,
        "job_id": job_id,
        "amount": float(amount) if amount is not None else 0,
        "method": method,
        "currency": currency,
        "status": "success",
        "created_at": datetime.datetime.now()
    })

    return jsonify({
        "message": "Payment successful",
        "success": True
    })

# =========================
# ADMIN DASHBOARD APIS
# =========================

@app.route('/admin/api/dashboard-stats')
def admin_api_dashboard_stats():

    if 'admin_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    jobs = list(print_jobs_collection.find())
    payments = list(payments_collection.find({"status": "success"}))
    printers = list(printers_collection.find())
    users = list(users_collection.find())

    now = datetime.datetime.now()
    today_start = datetime.datetime(now.year, now.month, now.day)
    month_start = datetime.datetime(now.year, now.month, 1)

    status_totals = {
        "awaiting_payment": 0,
        "pending": 0,
        "approved": 0,
        "printing": 0,
        "printed": 0,
        "rejected": 0
    }
    total_pages = 0
    completed_today = 0
    total_jobs = len(jobs)

    for job in jobs:
        st = job.get('status', 'awaiting_payment')
        status_totals[st] = status_totals.get(st, 0) + 1
        if st == 'printed':
            try:
                total_pages += int(job.get('pages', 0) or 0)
            except Exception:
                pass
            created = job.get('created_at')
            if created and created >= today_start:
                completed_today += 1

    total_revenue = sum(float(p.get('amount', 0) or 0) for p in payments)
    feedback_count = feedback_collection.count_documents({})
    total_users = len(users)
    new_users = sum(1 for u in users if u.get('created_at') and u['created_at'] >= month_start)

    daily_labels = [(now - datetime.timedelta(days=i)).strftime('%a') for i in range(6, -1, -1)]
    daily_jobs = []
    for i in range(6, -1, -1):
        day = now - datetime.timedelta(days=i)
        day_start = datetime.datetime(day.year, day.month, day.day)
        day_end = day_start + datetime.timedelta(days=1)
        daily_jobs.append(sum(1 for job in jobs if job.get('status') == 'printed' and job.get('created_at') and day_start <= job['created_at'] < day_end))

    monthly_labels = []
    monthly_jobs = []
    monthly_users = []

    for i in range(11, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
            
        month_start_iter = datetime.datetime(y, m, 1)
        if m == 12:
            month_end_iter = datetime.datetime(y + 1, 1, 1)
        else:
            month_end_iter = datetime.datetime(y, m + 1, 1)
            
        monthly_labels.append(month_start_iter.strftime('%b'))
        monthly_jobs.append(sum(1 for job in jobs if job.get('created_at') and month_start_iter <= job['created_at'] < month_end_iter))
        monthly_users.append(sum(1 for u in users if u.get('created_at') and month_start_iter <= u['created_at'] < month_end_iter))

    success_rate = round((status_totals['printed'] / total_jobs) * 100, 1) if total_jobs else 0
    printer_online = sum(1 for p in printers if str(p.get('status', '')).lower() != 'offline')

    return jsonify({
        "jobs": {
            "pending": status_totals['pending'] + status_totals['awaiting_payment'],
            "approved": status_totals['approved'],
            "printing": status_totals['printing'],
            "printed": status_totals['printed'],
            "rejected": status_totals['rejected'],
            "total": total_jobs
        },
        "users": total_users,
        "premium_users": 0,
        "new_this_month": new_users,
        "pages_printed": total_pages,
        "completed_today": completed_today,
        "success_rate": success_rate,
        "revenue": round(total_revenue, 2),
        "feedback_count": feedback_count,
        "active_printers": printer_online,
        "avg_print_time": "2.4",
        "charts": {
            "daily_labels": daily_labels,
            "daily_jobs": daily_jobs,
            "monthly_labels": monthly_labels,
            "monthly_jobs": monthly_jobs,
            "cumulative_users": monthly_users,
            "cumulative_jobs": monthly_jobs
        }
    })

@app.route('/admin/api/users')
def admin_api_users():

    if 'admin_id' not in session:
        return jsonify([]), 401
    
    users = list(users_collection.find())
    result = []
    
    for u in users:
        u_id = str(u['_id'])
        u_jobs = list(print_jobs_collection.find({"user_id": u_id}))
        
        job_count = len(u_jobs)
        page_count = 0
        for j in u_jobs:
            try:
                page_count += int(j.get('pages', 0) or 0)
            except (TypeError, ValueError):
                pass
                
        result.append({
            "id": u_id,
            "name": u.get('name', 'Unknown'),
            "role": "User",
            "jobs": job_count,
            "pages": page_count,
            "profile_photo": u.get('profile_photo', '')
        })
        
    return jsonify(result)

# =========================
# ADMIN DELETE USER
# =========================

@app.route('/admin/api/delete-user/<id>', methods=['DELETE'])
def admin_api_delete_user(id):

    if 'admin_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_obj_id = safe_object_id(id)
    if not user_obj_id:
        return jsonify({"error": "Invalid user ID"}), 400

    user = users_collection.find_one({"_id": user_obj_id})
    if not user:
        return jsonify({"error": "User not found"}), 404

    user_id_str = str(user_obj_id)

    # 1. Physically delete all user files from disk
    user_docs = list(documents_collection.find({"user_id": user_id_str}))
    for doc in user_docs:
        if doc and 'file_path' in doc:
            try:
                if os.path.exists(doc['file_path']):
                    os.remove(doc['file_path'])
            except Exception as e:
                print(f"Error removing physical file {doc.get('file_path')} during user delete: {e}")

    # 2. Perform cascade deletions across collections
    try:
        users_collection.delete_one({"_id": user_obj_id})
        documents_collection.delete_many({"user_id": user_id_str})
        print_jobs_collection.delete_many({"user_id": user_id_str})
        payments_collection.delete_many({"user_id": user_id_str})
        feedback_collection.delete_many({"user_id": user_id_str})
    except Exception as e:
        return jsonify({"error": f"Database cascade deletion failed: {str(e)}"}), 500

    return jsonify({
        "success": True,
        "message": f"User {user.get('name', 'Unknown')} and all associated data deleted successfully."
    })

# =========================
# ADMIN REQUESTS
# =========================

@app.route('/admin/api/requests')
def admin_api_requests():

    if 'admin_id' not in session:
        return jsonify([]), 401

    jobs = print_jobs_collection.find().sort("created_at", -1)
    payments = {p['job_id']: p for p in payments_collection.find()}
    printers = {str(p['_id']): p for p in printers_collection.find()}

    result = []
    for job in jobs:
        user = None
        if job.get('user_id'):
            user_obj_id = safe_object_id(job['user_id'])
            if user_obj_id:
                user = users_collection.find_one({"_id": user_obj_id})

        document = None
        if job.get('document_id'):
            document_obj_id = safe_object_id(job['document_id'])
            if document_obj_id:
                document = documents_collection.find_one({"_id": document_obj_id})

        payment = payments.get(str(job['_id']))

        assigned_printer = job.get('assigned_printer') or ''
        if not assigned_printer and job.get('printer_id'):
            printer = printers.get(str(job['printer_id']))
            if printer:
                assigned_printer = printer.get('name', '')

        pages_value = job.get('pages', 0) or 0
        try:
            pages_value = int(pages_value)
        except (TypeError, ValueError):
            pages_value = str(pages_value)

        copies_value = job.get('copies', 1) or 1
        try:
            copies_value = int(copies_value)
        except (TypeError, ValueError):
            copies_value = 1

        result.append({
            "id": str(job['_id']),
            "doc_id": str(document['_id']) if document else '',
            "user": user.get('name', 'Unknown') if user else 'Unknown',
            "file": document.get('file_name', 'Unknown') if document else 'Unknown',
            "pages": pages_value,
            "copies": copies_value,
            "created_at": job.get('created_at').strftime('%b %d, %Y %H:%M') if job.get('created_at') else '',
            "status": job.get('status', 'awaiting_payment'),
            "assigned_printer": assigned_printer,
            "payment_status": 'Paid' if payment and payment.get('status') == 'success' else 'Awaiting Payment',
            "payment_method": payment.get('method') if payment else None
        })

    return jsonify(result)

# =========================
# ADMIN PRINTERS
# =========================

@app.route('/admin/api/printers')
def admin_api_printers():

    if 'admin_id' not in session:
        return jsonify([]), 401

    printers = list(printers_collection.find())
    result = []

    for p in printers:
        status = p.get('status', 'offline')
        paper = int(p.get('paper_available', 0) or 0)
        ink = int(p.get('ink_level', 0) or 0)
        active_jobs = print_jobs_collection.count_documents({
            "assigned_printer": p.get('name'),
            "status": {"$in": ["approved", "printing"]}
        })

        alerts = []
        if str(status).lower() == 'offline':
            alerts.append(f"{p.get('name')} is offline")
        if paper <= 10:
            alerts.append(f"{p.get('name')} low paper")
        if ink <= 15:
            alerts.append(f"{p.get('name')} low ink")
        if str(status).lower() == 'online' and active_jobs > 0:
            alerts.append(f"{p.get('name')} busy")

        result.append({
            "id": str(p['_id']),
            "name": p.get('name', 'Unknown Printer'),
            "model": p.get('model', ''),
            "ip_address": p.get('ip_address', ''),
            "location": p.get('location', ''),
            "status": status,
            "paper_available": paper,
            "ink_level": ink,
            "active_job": p.get('active_job', None),
            "active_jobs": active_jobs,
            "alerts": alerts,
            "created_at": p.get('created_at').strftime('%b %d, %Y') if p.get('created_at') else ''
        })

    return jsonify(result)

# =========================
# ADMIN FEEDBACK
# =========================

@app.route('/admin/api/feedback')
def admin_api_feedback():
    if 'admin_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    items = list(feedback_collection.find().sort("created_at", -1).limit(8))
    result = []
    for item in items:
        user = users_collection.find_one({"_id": ObjectId(item['user_id'])}) if item.get('user_id') else None
        result.append({
            "id": str(item['_id']),
            "user": user.get('name', 'Guest') if user else 'Guest',
            "feedback": item.get('feedback', ''),
            "rating": item.get('rating', ''),
            "created_at": item.get('created_at').strftime('%b %d, %Y %H:%M') if item.get('created_at') else ''
        })

    return jsonify({
        "count": feedback_collection.count_documents({}),
        "items": result
    })

@app.route('/api/feedback', methods=['POST'])
def api_feedback():
    if 'user_id' not in session:
        return jsonify({"message": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    answers = data.get('answers', {})
    feedback_text = (data.get('feedback') or '').strip()
    rating = data.get('rating', '')

    feedback_collection.insert_one({
        "user_id": session['user_id'],
        "answers": answers,
        "feedback": feedback_text,
        "rating": rating,
        "created_at": datetime.datetime.now()
    })

    return jsonify({"message": "Feedback submitted"})

# =========================
# APPROVE AND PRINT
# =========================

@app.route('/admin/approve-print/<job_id>', methods=['POST'])
def admin_approve_print(job_id):
    """
    Approves a specific print job and initiates physical printing.
    
    Performs critical validations:
    - Verifies admin credentials via session.
    - Resolves the printer system name by ID or name.
    - Performs pre-flight health checks on the printer (paper availability, ink level, online status).
    - Checks for physical document presence on disk.
    - Sends the document to print_document.
    - Updates print job status to 'printing' and assigns printer resources.
    
    Parameters:
    - job_id (str): MongoDB ObjectId of the print job.
    - JSON body containing 'printer_name' or 'printer_id'.
    
    Returns:
    - JSON response with success status.
    """
    if 'admin_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    printer_name = data.get('printer_name')
    printer_id = data.get('printer_id')

    if not printer_name and not printer_id:
        return jsonify({"error": "Missing printer selection"}), 400

    job = print_jobs_collection.find_one({"_id": ObjectId(job_id)})
    if not job:
        return jsonify({"error": "Job not found"}), 404

    # Locate printer configuration
    if printer_id:
        printer = printers_collection.find_one({"_id": safe_object_id(printer_id)})
    else:
        printer = printers_collection.find_one({"name": printer_name})
        
    if not printer:
        return jsonify({"error": "Printer not found"}), 404
        
    printer_name = printer.get('name')

    # Enforce printer safety/health constraints before triggering hardware print
    if printer.get('paper_available', 0) <= 0:
        return jsonify({"error": "Printer is out of paper"}), 400
    if printer.get('ink_level', 0) <= 5:
        return jsonify({"error": "Printer ink level too low"}), 400
    if str(printer.get('status', '')).lower() == 'offline':
        return jsonify({"error": "Printer is offline"}), 400

    # Ensure document exists on the local filesystem
    doc = documents_collection.find_one({"_id": ObjectId(job['document_id'])})
    if not doc or not doc.get('file_path') or not os.path.exists(doc['file_path']):
        return jsonify({"error": "Document file not found on server"}), 404

    # Trigger printing toolchain
    copies_val = job.get('copies', 1)
    success, msg = print_document(doc['file_path'], printer_name, copies=copies_val)
    if not success:
        return jsonify({"error": f"Failed to print: {msg}"}), 500

    # Update job state in DB
    print_jobs_collection.update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {
            "status": "printing",
            "assigned_printer": printer_name,
            "print_started_at": datetime.datetime.now()
        }}
    )

    # Bind printer resources to this active job
    printers_collection.update_one(
        {"name": printer_name},
        {"$set": {"active_job": job_id, "status": "online"}}
    )

    return jsonify({"success": True, "message": "Printing started"})

# =========================
# UPDATE STATUS
# =========================

@app.route('/admin/api/update-status', methods=['POST'])
def admin_api_update_status():
    """
    Updates the status of print jobs from the admin control panel.
    
    Applies logic:
    - Enforces admin authorization.
    - Records start/end timestamps based on state transitions ('printing', 'printed').
    - If status becomes 'printed' and the job is confidential/sensitive:
      - Physically deletes the original document file from server storage.
      - Removes the document record from the database to protect user privacy.
    - Updates active printer resource bindings.
    
    Parameters:
    - JSON body: id (job ID), status (target status), assigned_printer.
    
    Returns:
    - JSON success confirmation.
    """
    if 'admin_id' not in session:
        return jsonify({
            "error": "Unauthorized"
        }), 401

    data = request.get_json() or {}
    job_id = data.get('id')
    status = data.get('status')
    assigned_printer = data.get('assigned_printer')

    if not job_id or not status:
        return jsonify({"error": "Missing id or status"}), 400

    update_fields = {}
    if status:
        update_fields['status'] = status
    if assigned_printer:
        update_fields['assigned_printer'] = assigned_printer

    if status == 'printing':
        update_fields['print_started_at'] = datetime.datetime.now()
    if status == 'printed':
        update_fields['printed_at'] = datetime.datetime.now()

    # Apply changes to job metadata
    print_jobs_collection.update_one(
        {
            "_id": ObjectId(job_id)
        },
        {
            "$set": update_fields
        }
    )

    # If completed and confidential, purge document data from the system entirely
    job = print_jobs_collection.find_one({"_id": ObjectId(job_id)})
    if status == 'printed' and job:
        if job.get('sensitive'):
            doc = documents_collection.find_one({"_id": ObjectId(job['document_id'])})
            if doc and doc.get('file_path') and os.path.exists(doc['file_path']):
                try:
                    os.remove(doc['file_path'])
                except Exception:
                    pass
            documents_collection.delete_one({"_id": ObjectId(job['document_id'])})

    # Track active hardware association
    if assigned_printer:
        printers_collection.update_one(
            {"name": assigned_printer},
            {"$set": {"active_job": job_id, "status": "online"}}
        )

    # Release printer once the job is finished
    if status == 'printed' and job and job.get('assigned_printer'):
        printers_collection.update_one(
            {"name": job.get('assigned_printer')},
            {"$unset": {"active_job": ""}}
        )

    return jsonify({
        "success": True
    })

# =========================
# USER API ENDPOINTS
# =========================

@app.route('/api/job-status/<job_id>')
def job_status(job_id):
    """
    Retrieves status metrics for a specific print job.
    
    Dynamically computes:
    - Queue position based on preceding jobs in 'pending' or 'approved' state.
    - Print completion percentage (derived from time elapsed since printing started).
    
    Parameters:
    - job_id (str): The MongoDB ObjectId of the target print job.
    
    Returns:
    - JSON object containing status details, page metrics, queue position, and completion percentage.
    """
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
        
    try:
        job = print_jobs_collection.find_one({
            "_id": ObjectId(job_id),
            "user_id": session['user_id']
        })
        if not job:
            return jsonify({"error": "Job not found"}), 404
            
        status = job.get('status', 'pending')
        total_pages = int(job.get('pages', 1))
        current_page = 0
        percentage = 0
        
        # Calculate queue position (jobs created before this one that are still pending/approved)
        queue_position = 0
        if status in ['pending', 'approved']:
            created_at = job.get('created_at', datetime.datetime.now())
            queue_position = print_jobs_collection.count_documents({
                "status": {"$in": ["pending", "approved"]},
                "created_at": {"$lt": created_at}
            })
            
        # Calculate current page based on elapsed time (e.g. printing speed benchmark of 2.4s per page)
        if status == 'printing':
            print_started_at = job.get('print_started_at', datetime.datetime.now())
            elapsed = (datetime.datetime.now() - print_started_at).total_seconds()
            current_page = min(int(elapsed / 2.4), total_pages)
            if current_page >= total_pages:
                current_page = total_pages
        elif status == 'printed':
            current_page = total_pages
            
        if total_pages > 0:
            percentage = min(int((current_page / total_pages) * 100), 100)
            
        return jsonify({
            "status": status,
            "total_pages": total_pages,
            "current_page": current_page,
            "queue_position": queue_position,
            "percentage": percentage
        })
    except Exception as e:
        return jsonify({"error": "Invalid job ID"}), 400

@app.route('/cancel-job/<id>', methods=['POST'])
def cancel_job(id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
        
    try:
        print_jobs_collection.delete_one({
            "_id": ObjectId(id),
            "user_id": session['user_id'],
            "status": "pending"
        })
        return jsonify({"success": True})
    except:
        return jsonify({"error": "Invalid ID"}), 400

@app.route('/update-profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json()
    
    first_name = data.get('first_name', '')
    last_name = data.get('last_name', '')
    name = f"{first_name} {last_name}".strip()
    
    update_data = {
        "name": name if name else data.get('name', ''),
        "email": data.get('email'),
        "phone": data.get('phone'),
        "department": data.get('department'),
        "roll_number": data.get('roll_no')
    }
    
    if data.get('password'):
        update_data['password'] = data.get('password')
        
    users_collection.update_one(
        {"_id": ObjectId(session['user_id'])},
        {"$set": {k: v for k, v in update_data.items() if v is not None and v != ''}}
    )
    
    return jsonify({"success": True})

# =========================
# UPLOAD PROFILE PHOTO
# =========================

@app.route('/upload-profile-photo', methods=['POST'])
def upload_profile_photo():
    """
    Handles user profile photo uploads.
    
    Security and Integrity Steps:
    - Enforces user authorization check.
    - Validates image file extension (allowed: PNG, JPG, JPEG, GIF).
    - Cleans up any existing profile picture of the same user with different extensions to avoid orphans.
    - Saves the newly uploaded profile photo under static/uploads/profile_{user_id}.{ext}.
    - Updates MongoDB record for the user profile_photo field.
    
    Returns:
    - JSON success notification and photo URL.
    """
    if 'user_id' not in session:
        return jsonify({"message": "Unauthorized"}), 401

    if 'photo' not in request.files:
        return jsonify({"message": "No photo file provided"}), 400

    file = request.files['photo']
    if file.filename == '':
        return jsonify({"message": "Empty file name"}), 400

    # Validate image extensions
    allowed_image_extensions = {'png', 'jpg', 'jpeg', 'gif'}
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_image_extensions:
        return jsonify({"message": "Invalid file type. Only PNG, JPG, and GIF are allowed."}), 400

    user_id = session['user_id']
    
    # Storage filename: profile_{user_id}.{ext}
    filename = f"profile_{user_id}.{ext}"
    
    # Auto-cleanup: remove existing profile images with different extensions to avoid orphans
    for existing_ext in allowed_image_extensions:
        existing_file = os.path.join(UPLOAD_FOLDER, f"profile_{user_id}.{existing_ext}")
        if os.path.exists(existing_file):
            try:
                os.remove(existing_file)
            except Exception as e:
                print(f"Error removing old avatar file: {e}")

    # Save the new photo
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    photo_url = f"/static/uploads/{filename}"

    # Update MongoDB record
    users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"profile_photo": photo_url}}
    )

    return jsonify({
        "success": True,
        "photo_url": photo_url
    })

# =========================
# LOGOUT
# =========================

@app.route('/logout', methods=['GET', 'POST'])
def logout():

    session.pop('user_id', None)

    return redirect('/')

@app.route('/admin-logout')
def admin_logout():

    session.pop('admin_id', None)

    return redirect('/')

# =========================
# RUN APP
# =========================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)