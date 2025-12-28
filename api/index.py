# --- api/index.py ---

# 1. FORCE IPv4 (Must be at the very top)
import socket
import os

# This 'monkey patch' filters out IPv6 addresses so Flask only sees IPv4
# It fixes the "Cannot assign requested address" error on Vercel
valid_families = (socket.AF_INET,)
original_getaddrinfo = socket.getaddrinfo

def new_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    # Force the query to use IPv4 (AF_INET)
    return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = new_getaddrinfo
# ----------------------------------------

# 2. Regular imports start here
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.pool import NullPool

# ... (Rest of your code remains exactly the same) ...


# GET ABSOLUTE PATH TO TEMPLATES
# This calculates the path /var/task/templates based on the current file location
template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))

# Initialize Flask with the absolute path
app = Flask(__name__, template_folder=template_dir)
# Security: Use Environment Variable or fallback for dev
app.secret_key = os.environ.get('SECRET_KEY', 'default_dev_key')
# --- api/index.py (Updated DB Section) ---

# ... imports ...

app = Flask(__name__, template_folder='../templates')

# Security
app.secret_key = os.environ.get('SECRET_KEY', 'default_dev_key')
# --- SUPABASE DATABASE CONFIGURATION ---
db_url = os.environ.get('DATABASE_URL')

if db_url:
    # Fix 1: Ensure correct protocol
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    # Fix 2: Ensure SSL is required
    if "sslmode" not in db_url:
        separator = "&" if "?" in db_url else "?"
        db_url += f"{separator}sslmode=require"

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Fix 3: Disable SQLAlchemy Pooling (Let Supabase handle it)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "poolclass": NullPool,       # <--- Critical for Port 6543
    "pool_pre_ping": True,       # Checks connection health
    "connect_args": {
        "connect_timeout": 10    # Fail fast if connection hangs
    }
}

db = SQLAlchemy(app)

# ... (Rest of the code: Models, Routes, etc. remain exactly the same) ...

# --- Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    ip_address = db.Column(db.String(50))
    is_admin_approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    comparisons = db.relationship('Comparison', backref='author', lazy=True)

class City(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    country = db.Column(db.String(50), nullable=False)
    population = db.Column(db.Integer)

class Comparison(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    city_us_id = db.Column(db.Integer, db.ForeignKey('city.id'), nullable=False)
    city_tr_id = db.Column(db.Integer, db.ForeignKey('city.id'), nullable=False)
    sim1 = db.Column(db.String(20))
    sim2 = db.Column(db.String(20))
    sim3 = db.Column(db.String(20))
    sim4 = db.Column(db.String(20))
    sim5 = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    city_us = db.relationship('City', foreign_keys=[city_us_id])
    city_tr = db.relationship('City', foreign_keys=[city_tr_id])
    __table_args__ = (db.UniqueConstraint('user_id', 'city_us_id', 'city_tr_id', name='unique_user_city_pair'),)

# --- Routes ---

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        if not username:
            flash("Username required")
            return redirect(url_for('login'))
        
        user = User.query.filter_by(username=username).first()
        if not user:
            # Note: request.remote_addr might be a proxy IP on Vercel
            user = User(username=username, ip_address=request.headers.get('x-forwarded-for', request.remote_addr))
            db.session.add(user)
            db.session.commit()
        else:
            user.ip_address = request.headers.get('x-forwarded-for', request.remote_addr)
            db.session.commit()
        
        session['user_id'] = user.id
        session['username'] = user.username
        return redirect(url_for('dashboard'))
        
    return render_template('login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            us_id = request.form.get('us_city')
            tr_id = request.form.get('tr_city')
            sims = [request.form.get(f'sim{i}')[:20] for i in range(1, 6)]
            
            exists = Comparison.query.filter_by(user_id=user.id, city_us_id=us_id, city_tr_id=tr_id).first()
            if exists:
                flash("Comparison already exists.")
            else:
                new_comp = Comparison(user_id=user.id, city_us_id=us_id, city_tr_id=tr_id,
                                      sim1=sims[0], sim2=sims[1], sim3=sims[2], sim4=sims[3], sim5=sims[4])
                db.session.add(new_comp)
                db.session.commit()
                flash("Added successfully!")

        elif action == 'edit':
            comp_id = request.form.get('comp_id')
            comp = Comparison.query.get(comp_id)
            if comp and comp.user_id == user.id:
                comp.sim1 = request.form.get('sim1')[:20]
                comp.sim2 = request.form.get('sim2')[:20]
                comp.sim3 = request.form.get('sim3')[:20]
                comp.sim4 = request.form.get('sim4')[:20]
                comp.sim5 = request.form.get('sim5')[:20]
                db.session.commit()
                flash("Updated.")
        
        elif action == 'delete':
            comp_id = request.form.get('comp_id')
            comp = Comparison.query.get(comp_id)
            if comp and comp.user_id == user.id:
                db.session.delete(comp)
                db.session.commit()
                flash("Deleted.")
            
    us_cities = City.query.filter_by(country='USA').order_by(City.name).all()
    tr_cities = City.query.filter_by(country='Turkiye').order_by(City.name).all()
    my_comparisons = Comparison.query.filter_by(user_id=user.id).order_by(Comparison.created_at.desc()).all()
    
    return render_template('dashboard.html', user=user, us_cities=us_cities, tr_cities=tr_cities, comparisons=my_comparisons, count=len(my_comparisons))

@app.route('/list')
def list_view():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    user_contrib_count = Comparison.query.filter_by(user_id=user.id).count()
    if user_contrib_count < 10 and not user.is_admin_approved:
        flash(f"Access Denied. Need 10 contributions. You have {user_contrib_count}.")
        return redirect(url_for('dashboard'))

    filter_us = request.args.get('filter_us')
    filter_tr = request.args.get('filter_tr')
    query = Comparison.query
    if filter_us: query = query.filter(Comparison.city_us_id == filter_us)
    if filter_tr: query = query.filter(Comparison.city_tr_id == filter_tr)
        
    all_comparisons = query.join(User).add_columns(User.username).all()
    us_cities = City.query.filter_by(country='USA').order_by(City.name).all()
    tr_cities = City.query.filter_by(country='Turkiye').order_by(City.name).all()
    return render_template('restricted_list.html', comparisons=all_comparisons, us_cities=us_cities, tr_cities=tr_cities)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- DB Setup Route (Run this once) ---
@app.route('/setup_db')
def setup_db():
    try:
        db.create_all()
        if not City.query.first():
            # (Truncated list for brevity, full list acts the same)
            us_cities = ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Jose", "Austin", "Jacksonville", "Fort Worth", "Columbus", "Charlotte", "San Francisco", "Indianapolis", "Seattle", "Denver", "Washington", "Boston", "El Paso", "Nashville", "Detroit", "Oklahoma City", "Portland", "Las Vegas", "Memphis", "Louisville", "Baltimore", "Milwaukee", "Albuquerque", "Tucson", "Fresno", "Mesa", "Sacramento", "Atlanta", "Kansas City", "Colorado Springs", "Miami", "Raleigh", "Omaha", "Long Beach", "Virginia Beach", "Oakland", "Minneapolis", "Tulsa", "Arlington", "Tampa", "New Orleans"]
            tr_cities = ["Istanbul", "Ankara", "Izmir", "Bursa", "Adana", "Gaziantep", "Konya", "Antalya", "Kayseri", "Mersin", "Eskisehir", "Diyarbakir", "Samsun", "Denizli", "Sanliurfa", "Malatya", "Kahramanmaras", "Erzurum", "Van", "Batman", "Elazig", "Izmit", "Manisa", "Sivas", "Gebze", "Balikesir", "Tarsus", "Trabzon", "Kutahya", "Corum", "Isparta", "Osmaniye", "Kirikkale", "Antakya", "Aydin", "Iskenderun", "Usak", "Aksaray", "Afyon", "Edirne"]
            
            for name in us_cities: db.session.add(City(name=name, country='USA', population=100000))
            for name in tr_cities: db.session.add(City(name=name, country='Turkiye', population=100000))
            db.session.commit()
            return "Database initialized and seeded!"
        return "Database already exists."
    except Exception as e:
        return f"Error: {str(e)}"