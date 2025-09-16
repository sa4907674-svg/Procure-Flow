# app.py
# A single-file Flask app you can deploy on free tiers (Render, Railway, Replit, etc.)
# Features: Roles (Admin/Procurement/Vendor/Branch), Tasks, Deliveries, Payments, Status dashboards.
# DB: SQLite (file). Auth: username/password with role. Bootstrap UI.

import os
from datetime import datetime, date
from flask import Flask, redirect, render_template_string, request, url_for, session, flash, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database.sqlite3')

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-this')

db = SQLAlchemy(app)

# --- Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # admin, procurement, vendor, branch
    vendor_name = db.Column(db.String(120))  # for vendor role
    branch_name = db.Column(db.String(120))  # for branch role

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    vendor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quantity = db.Column(db.Float, default=0)
    unit = db.Column(db.String(20), default='pcs')
    price_per_unit = db.Column(db.Float, default=0)
    due_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='Pending')  # Pending, In Progress, Completed, Cancelled
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)
    amount = db.Column(db.Float, default=0)
    due_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='Pending')  # Pending, Paid, Overdue
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Delivery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)
    to_branch = db.Column(db.String(120), nullable=False)
    shipped_qty = db.Column(db.Float, default=0)
    received_qty = db.Column(db.Float, default=0)
    damage_notes = db.Column(db.Text)
    status = db.Column(db.String(20), default='Not Shipped')  # Not Shipped, Shipped, Received
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# --- Helpers ---
def current_user():
    uid = session.get('uid')
    if uid:
        return User.query.get(uid)
    return None

def login_required(roles=None):
    def wrapper(fn):
        def inner(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for('login', next=request.path))
            if roles and user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        inner.__name__ = fn.__name__
        return inner
    return wrapper

# --- Routes ---
@app.route('/')
@login_required()
def index():
    user = current_user()
    if user.role in ['admin', 'procurement']:
        tasks = Task.query.order_by(Task.created_at.desc()).all()
        payments = Payment.query.all()
        deliveries = Delivery.query.all()
    elif user.role == 'vendor':
        tasks = Task.query.filter_by(vendor_id=user.id).order_by(Task.created_at.desc()).all()
        task_ids = [t.id for t in tasks]
        payments = Payment.query.filter(Payment.task_id.in_(task_ids)).all()
        deliveries = Delivery.query.filter(Delivery.task_id.in_(task_ids)).all()
    else:  # branch
        deliveries = Delivery.query.filter_by(to_branch=user.branch_name).order_by(Delivery.updated_at.desc()).all()
        task_ids = [d.task_id for d in deliveries]
        tasks = Task.query.filter(Task.id.in_(task_ids)).all()
        payments = Payment.query.filter(Payment.task_id.in_(task_ids)).all()

    overdue = [p for p in payments if p.status != 'Paid' and p.due_date and p.due_date < date.today()]
    return render_template_string(TPL_DASH, user=user, tasks=tasks, payments=payments, deliveries=deliveries, overdue=len(overdue))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        pw = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(pw):
            session['uid'] = user.id
            flash('Welcome, ' + user.username)
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid credentials', 'danger')
    return render_template_string(TPL_LOGIN)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/users', methods=['GET', 'POST'])
@login_required(['admin'])
def users():
    if request.method == 'POST':
        u = User(username=request.form['username'].strip(), role=request.form['role'])
        if request.form.get('vendor_name'):
            u.vendor_name = request.form['vendor_name']
        if request.form.get('branch_name'):
            u.branch_name = request.form['branch_name']
        u.set_password(request.form['password'])
        db.session.add(u)
        db.session.commit()
        flash('User created')
        return redirect(url_for('users'))
    return render_template_string(TPL_USERS, users=User.query.all())

@app.route('/tasks/new', methods=['GET', 'POST'])
@login_required(['admin','procurement'])
def task_new():
    if request.method == 'POST':
        t = Task(
            title=request.form['title'],
            description=request.form.get('description'),
            vendor_id=int(request.form['vendor_id']),
            quantity=float(request.form.get('quantity') or 0),
            unit=request.form.get('unit') or 'pcs',
            price_per_unit=float(request.form.get('price_per_unit') or 0),
            due_date=datetime.strptime(request.form['due_date'], '%Y-%m-%d').date() if request.form.get('due_date') else None,
            created_by=current_user().id,
        )
        db.session.add(t)
        db.session.commit()
        # Create default payment + delivery shells
        db.session.add(Payment(task_id=t.id, amount=t.quantity * t.price_per_unit))
        db.session.add(Delivery(task_id=t.id, to_branch=request.form.get('to_branch', 'Main Branch')))
        db.session.commit()
        flash('Task created')
        return redirect(url_for('index'))
    vendors = User.query.filter_by(role='vendor').all()
    return render_template_string(TPL_TASK_NEW, vendors=vendors)

@app.route('/tasks/<int:task_id>', methods=['GET','POST'])
@login_required()
def task_view(task_id):
    t = Task.query.get_or_404(task_id)
    user = current_user()
    # Permissions: vendor can update only their task status; branch can update delivery; procurement/admin can update all.
    pay = Payment.query.filter_by(task_id=t.id).first()
    deliv = Delivery.query.filter_by(task_id=t.id).first()

    if request.method == 'POST':
        form = request.form
        if 'update_task' in form and user.role in ['admin','procurement','vendor']:
            if user.role == 'vendor' and t.vendor_id != user.id:
                abort(403)
            t.status = form.get('status', t.status)
            t.due_date = datetime.strptime(form['due_date'], '%Y-%m-%d').date() if form.get('due_date') else t.due_date
            db.session.commit()
            flash('Task updated')
        if 'update_payment' in form and user.role in ['admin','procurement']:
            pay.amount = float(form.get('amount') or pay.amount)
            pay.due_date = datetime.strptime(form['pay_due'], '%Y-%m-%d').date() if form.get('pay_due') else pay.due_date
            pay.status = form.get('pay_status', pay.status)
            db.session.commit()
            flash('Payment updated')
        if 'update_delivery' in form and user.role in ['admin','procurement','branch']:
            if user.role == 'branch' and user.branch_name != deliv.to_branch:
                abort(403)
            deliv.to_branch = form.get('to_branch') or deliv.to_branch
            deliv.shipped_qty = float(form.get('shipped_qty') or deliv.shipped_qty)
            deliv.received_qty = float(form.get('received_qty') or deliv.received_qty)
            deliv.damage_notes = form.get('damage_notes')
            deliv.status = form.get('deliv_status', deliv.status)
            db.session.commit()
            flash('Delivery updated')
        return redirect(url_for('task_view', task_id=t.id))

    vendor = User.query.get(t.vendor_id)
    return render_template_string(TPL_TASK_VIEW, t=t, vendor=vendor, pay=pay, deliv=deliv, user=user)

# --- Templates as Python strings (for 1-file simplicity) ---
TPL_BASE = """
<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>ProcureFlow</title>
  <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css' rel='stylesheet'>
  <style>
    body { padding-top: 70px; }
    .badge-role { text-transform: capitalize; }
  </style>
</head>
<body>
<nav class='navbar navbar-expand-lg navbar-dark bg-dark fixed-top'>
  <div class='container-fluid'>
    <a class='navbar-brand' href='{{ url_for("index") }}'>ProcureFlow</a>
    <button class='navbar-toggler' type='button' data-bs-toggle='collapse' data-bs-target='#nav'>
      <span class='navbar-toggler-icon'></span>
    </button>
    <div class='collapse navbar-collapse' id='nav'>
      <ul class='navbar-nav me-auto'>
        {% if user and user.role in ['admin','procurement'] %}
        <li class='nav-item'><a class='nav-link' href='{{ url_for("task_new") }}'>New Task</a></li>
        {% endif %}
        {% if user and user.role == 'admin' %}
        <li class='nav-item'><a class='nav-link' href='{{ url_for("users") }}'>Users</a></li>
        {% endif %}
      </ul>
      <span class='navbar-text me-3'>
        {% if user %}
          <span class='badge bg-info badge-role'>{{ user.role }}</span>
        {% endif %}
      </span>
      <ul class='navbar-nav'>
        {% if user %}
        <li class='nav-item'><a class='nav-link' href='{{ url_for("logout") }}'>Logout</a></li>
        {% endif %}
      </ul>
    </div>
  </div>
</nav>
<div class='container'>
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      <div class='mt-2'>
      {% for cat, msg in messages %}
        <div class='alert alert-{{ 'warning' if cat=='message' else cat }}'>{{ msg }}</div>
      {% endfor %}
      </div>
    {% endif %}
  {% endwith %}
  {% block content %}{% endblock %}
</div>
<script src='https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js'></script>
</body></html>
"""

TPL_LOGIN = """
<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css' rel='stylesheet'>
<title>Login – ProcureFlow</title></head>
<body class='bg-light'>
<div class='container d-flex justify-content-center align-items-center' style='min-height:100vh;'>
  <div class='card shadow p-4' style='max-width:400px;width:100%'>
    <h3 class='mb-3'>Sign in</h3>
    <form method='post'>
      <div class='mb-3'><label class='form-label'>Username</label><input name='username' class='form-control' required></div>
      <div class='mb-3'><label class='form-label'>Password</label><input name='password' type='password' class='form-control' required></div>
      <button class='btn btn-primary w-100'>Login</button>
    </form>
    <p class='text-muted mt-3'>Default admin: <code>admin / admin123</code> (will be created on first run).</p>
  </div>
</div>
</body></html>
"""

TPL_DASH = """
{% extends TPL_BASE %}{% block content %}
<h3 class='mb-3'>Dashboard</h3>
<div class='row g-3'>
  <div class='col-md-4'>
    <div class='card shadow-sm'><div class='card-body'>
      <h5 class='card-title'>Tasks</h5>
      <p class='text-muted'>Total: {{ tasks|length }}</p>
      <a href='#tasks' class='btn btn-sm btn-outline-primary'>View below</a>
    </div></div>
  </div>
  <div class='col-md-4'>
    <div class='card shadow-sm'><div class='card-body'>
      <h5 class='card-title'>Payments</h5>
      <p class='text-muted'>Overdue: {{ overdue }}</p>
      <a href='#payments' class='btn btn-sm btn-outline-primary'>View below</a>
    </div></div>
  </div>
  <div class='col-md-4'>
    <div class='card shadow-sm'><div class='card-body'>
      <h5 class='card-title'>Deliveries</h5>
      <p class='text-muted'>Tracked: {{ deliveries|length }}</p>
      <a href='#deliveries' class='btn btn-sm btn-outline-primary'>View below</a>
    </div></div>
  </div>
</div>

<hr>
<h4 id='tasks'>Tasks</h4>
<table class='table table-striped table-hover'>
  <thead><tr><th>ID</th><th>Title</th><th>Vendor</th><th>Qty</th><th>Due</th><th>Status</th><th></th></tr></thead>
  <tbody>
  {% for t in tasks %}
    <tr>
      <td>{{ t.id }}</td>
      <td>{{ t.title }}</td>
      <td>{{ (users := namespace(v=None)) or (users.v := (User.query.get(t.vendor_id))) or '' }}{{ users.v.vendor_name or users.v.username }}</td>
      <td>{{ t.quantity }} {{ t.unit }}</td>
      <td>{{ t.due_date or '' }}</td>
      <td><span class='badge bg-secondary'>{{ t.status }}</span></td>
      <td><a class='btn btn-sm btn-outline-dark' href='{{ url_for("task_view", task_id=t.id) }}'>Open</a></td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<h4 id='payments' class='mt-4'>Payments</h4>
<table class='table table-sm'>
  <thead><tr><th>Task</th><th>Amount</th><th>Due</th><th>Status</th></tr></thead>
  <tbody>
  {% for p in payments %}
    <tr>
      <td><a href='{{ url_for("task_view", task_id=p.task_id) }}'>#{{ p.task_id }}</a></td>
      <td>£{{ '%.2f'|format(p.amount) }}</td>
      <td>{{ p.due_date or '' }}</td>
      <td><span class='badge {{ 'bg-danger' if p.status=='Overdue' else ('bg-success' if p.status=='Paid' else 'bg-warning text-dark') }}'>{{ p.status }}</span></td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<h4 id='deliveries' class='mt-4'>Deliveries</h4>
<table class='table table-sm'>
  <thead><tr><th>Task</th><th>To Branch</th><th>Shipped</th><th>Received</th><th>Status</th></tr></thead>
  <tbody>
  {% for d in deliveries %}
    <tr>
      <td><a href='{{ url_for("task_view", task_id=d.task_id) }}'>#{{ d.task_id }}</a></td>
      <td>{{ d.to_branch }}</td>
      <td>{{ d.shipped_qty }}</td>
      <td>{{ d.received_qty }}</td>
      <td><span class='badge {{ 'bg-success' if d.status=='Received' else ('bg-info' if d.status=='Shipped' else 'bg-secondary') }}'>{{ d.status }}</span></td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
"""

TPL_USERS = """
{% extends TPL_BASE %}{% block content %}
<h3>Users</h3>
<form method='post' class='row g-3 mb-4'>
  <div class='col-md-3'><label class='form-label'>Username</label><input name='username' class='form-control' required></div>
  <div class='col-md-3'><label class='form-label'>Password</label><input name='password' type='password' class='form-control' required></div>
  <div class='col-md-2'><label class='form-label'>Role</label>
    <select name='role' class='form-select' required>
      <option value='procurement'>procurement</option>
      <option value='vendor'>vendor</option>
      <option value='branch'>branch</option>
      <option value='admin'>admin</option>
    </select>
  </div>
  <div class='col-md-2'><label class='form-label'>Vendor Name</label><input name='vendor_name' class='form-control' placeholder='for vendor role'></div>
  <div class='col-md-2'><label class='form-label'>Branch Name</label><input name='branch_name' class='form-control' placeholder='for branch role'></div>
  <div class='col-12'><button class='btn btn-primary'>Create User</button></div>
</form>

<table class='table table-striped'>
  <thead><tr><th>ID</th><th>Username</th><th>Role</th><th>Vendor</th><th>Branch</th></tr></thead>
  <tbody>
    {% for u in users %}
    <tr>
      <td>{{ u.id }}</td><td>{{ u.username }}</td>
      <td>{{ u.role }}</td><td>{{ u.vendor_name or '' }}</td><td>{{ u.branch_name or '' }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
"""

TPL_TASK_NEW = """
{% extends TPL_BASE %}{% block content %}
<h3>New Task</h3>
<form method='post' class='row g-3'>
  <div class='col-md-6'><label class='form-label'>Title</label><input name='title' class='form-control' required></div>
  <div class='col-md-6'><label class='form-label'>Vendor</label>
    <select name='vendor_id' class='form-select' required>
      {% for v in vendors %}
        <option value='{{ v.id }}'>{{ v.vendor_name or v.username }}</option>
      {% endfor %}
    </select>
  </div>
  <div class='col-md-12'><label class='form-label'>Description</label><textarea name='description' class='form-control'></textarea></div>
  <div class='col-md-3'><label class='form-label'>Quantity</label><input name='quantity' type='number' step='0.01' class='form-control'></div>
  <div class='col-md-3'><label class='form-label'>Unit</label><input name='unit' class='form-control' value='pcs'></div>
  <div class='col-md-3'><label class='form-label'>Price / Unit</label><input name='price_per_unit' type='number' step='0.01' class='form-control'></div>
  <div class='col-md-3'><label class='form-label'>Due Date</label><input name='due_date' type='date' class='form-control'></div>
  <div class='col-md-6'><label class='form-label'>Deliver To Branch</label><input name='to_branch' class='form-control' value='Main Branch'></div>
  <div class='col-12'><button class='btn btn-success'>Create</button></div>
</form>
{% endblock %}
"""

TPL_TASK_VIEW = """
{% extends TPL_BASE %}{% block content %}
<h3>Task #{{ t.id }} – {{ t.title }}</h3>
<p class='text-muted'>Vendor: <strong>{{ vendor.vendor_name or vendor.username }}</strong> | Created: {{ t.created_at.strftime('%Y-%m-%d') }}</p>
<div class='row g-3'>
  <div class='col-md-6'>
    <div class='card shadow-sm'>
      <div class='card-header'>Task Details</div>
      <div class='card-body'>
        <p>{{ t.description or 'No description' }}</p>
        <ul class='list-unstyled'>
          <li>Quantity: <strong>{{ t.quantity }} {{ t.unit }}</strong></li>
          <li>Price/Unit: <strong>£{{ '%.2f'|format(t.price_per_unit) }}</strong></li>
          <li>Due: <strong>{{ t.due_date or '—' }}</strong></li>
          <li>Status: <span class='badge bg-secondary'>{{ t.status }}</span></li>
        </ul>
        {% if user.role in ['admin','procurement'] or (user.role=='vendor' and user.id==t.vendor_id) %}
        <form method='post' class='row g-2'>
          <input type='hidden' name='update_task' value='1'>
          <div class='col-md-6'>
            <label class='form-label'>Status</label>
            <select name='status' class='form-select'>
              {% for s in ['Pending','In Progress','Completed','Cancelled'] %}
                <option value='{{ s }}' {% if t.status==s %}selected{% endif %}>{{ s }}</option>
              {% endfor %}
            </select>
          </div>
          <div class='col-md-6'>
            <label class='form-label'>Due Date</label>
            <input type='date' name='due_date' class='form-control' value='{{ t.due_date }}'>
          </div>
          <div class='col-12'><button class='btn btn-primary btn-sm'>Save Task</button></div>
        </form>
        {% endif %}
      </div>
    </div>
  </div>

  <div class='col-md-6'>
    <div class='card shadow-sm'>
      <div class='card-header'>Payment</div>
      <div class='card-body'>
        {% if pay %}
          <ul class='list-unstyled'>
            <li>Amount: <strong>£{{ '%.2f'|format(pay.amount) }}</strong></li>
            <li>Due: <strong>{{ pay.due_date or '—' }}</strong></li>
            <li>Status: <span class='badge {{ 'bg-success' if pay.status=='Paid' else ('bg-danger' if pay.status=='Overdue' else 'bg-warning text-dark') }}'>{{ pay.status }}</span></li>
          </ul>
          {% if user.role in ['admin','procurement'] %}
          <form method='post' class='row g-2'>
            <input type='hidden' name='update_payment' value='1'>
            <div class='col-md-4'><label class='form-label'>Amount</label><input name='amount' type='number' step='0.01' class='form-control' value='{{ pay.amount }}'></div>
            <div class='col-md-4'><label class='form-label'>Due</label><input name='pay_due' type='date' class='form-control' value='{{ pay.due_date }}'></div>
            <div class='col-md-4'><label class='form-label'>Status</label>
              <select name='pay_status' class='form-select'>
                {% for s in ['Pending','Paid','Overdue'] %}<option value='{{ s }}' {% if pay.status==s %}selected{% endif %}>{{ s }}</option>{% endfor %}
              </select>
            </div>
            <div class='col-12'><button class='btn btn-primary btn-sm'>Save Payment</button></div>
          </form>
          {% endif %}
        {% else %}
          <p class='text-muted'>No payment record.</p>
        {% endif %}
      </div>
    </div>
  </div>

  <div class='col-md-12'>
    <div class='card shadow-sm mt-3'>
      <div class='card-header'>Delivery</div>
      <div class='card-body'>
        {% if deliv %}
        <ul class='list-unstyled'>
          <li>To Branch: <strong>{{ deliv.to_branch }}</strong></li>
          <li>Shipped Qty: <strong>{{ deliv.shipped_qty }}</strong> | Received Qty: <strong>{{ deliv.received_qty }}</strong></li>
          <li>Status: <span class='badge {{ 'bg-success' if deliv.status=='Received' else ('bg-info' if deliv.status=='Shipped' else 'bg-secondary') }}'>{{ deliv.status }}</span></li>
          <li>Damage Notes: {{ deliv.damage_notes or '—' }}</li>
        </ul>
        {% if user.role in ['admin','procurement','branch'] %}
        <form method='post' class='row g-2'>
          <input type='hidden' name='update_delivery' value='1'>
          <div class='col-md-3'><label class='form-label'>To Branch</label><input name='to_branch' class='form-control' value='{{ deliv.to_branch }}'></div>
          <div class='col-md-3'><label class='form-label'>Shipped Qty</label><input name='shipped_qty' type='number' step='0.01' class='form-control' value='{{ deliv.shipped_qty }}'></div>
          <div class='col-md-3'><label class='form-label'>Received Qty</label><input name='received_qty' type='number' step='0.01' class='form-control' value='{{ deliv.received_qty }}'></div>
          <div class='col-md-3'><label class='form-label'>Status</label>
            <select name='deliv_status' class='form-select'>
              {% for s in ['Not Shipped','Shipped','Received'] %}<option value='{{ s }}' {% if deliv.status==s %}selected{% endif %}>{{ s }}</option>{% endfor %}
            </select>
          </div>
          <div class='col-12'><label class='form-label'>Damage Notes</label><textarea name='damage_notes' class='form-control'>{{ deliv.damage_notes or '' }}</textarea></div>
          <div class='col-12'><button class='btn btn-primary btn-sm'>Save Delivery</button></div>
        </form>
        {% endif %}
        {% else %}
        <p class='text-muted'>No delivery record.</p>
        {% endif %}
      </div>
    </div>
  </div>
</div>
{% endblock %}
"""

# Jinja needs base template in globals
app.jinja_env.globals['TPL_BASE'] = TPL_BASE
app.jinja_env.globals['User'] = User

# --- CLI / Setup ---
@app.cli.command('initdb')
def initdb():
    """Initialize the database and create a default admin user."""
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print('Created default admin: admin / admin123')
    else:
        print('Admin already exists')

if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        with app.app_context():
            db.create_all()
            if not User.query.filter_by(username='admin').first():
                admin = User(username='admin', role='admin')
                admin.set_password('admin123')
                db.session.add(admin)
                db.session.commit()
                print('Created default admin: admin / admin123')
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
