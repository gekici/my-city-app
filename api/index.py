import os
import socket
from urllib.parse import urlparse, urlunparse
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.pool import NullPool

# 1. SETUP PATHS
template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))
app = Flask(__name__, template_folder=template_dir)
app.secret_key = os.environ.get('SECRET_KEY', 'default_dev_key')

# 2. STRICT DATABASE CONNECTION LOGIC
def get_safe_db_url():
    raw_url = os.environ.get('DATABASE_URL')
    if not raw_url:
        return 'sqlite:///local.db'

    try:
        # Parse the URL
        parsed = urlparse(raw_url)
        hostname = parsed.hostname
        port = parsed.port or 5432
        
        # STRICTLY resolve to IPv4 (AF_INET)
        # We use getaddrinfo to specifically ask for 'A' records (IPv4)
        # This prevents getting an IPv6 address by mistake
        try:
            addr_info = socket.getaddrinfo(hostname, port, socket.AF_INET, socket.SOCK_STREAM)
            # The result is a list of tuples. We take the IP address from the first one.
            # Format: (family, type, proto, canonname, (address, port))
            ipv4_address = addr_info[0][4][0]
            print(f"Resolved {hostname} to IPv4: {ipv4_address}")
        except socket.gaierror as e:
            print(f"DNS Resolution Failed for {hostname}: {e}")
            raise e

        # Reconstruct the URL with the raw IPv4 address
        # We manually rebuild the netloc to handle special chars in passwords safely
        user = parsed.username
        password = parsed.password
        new_netloc = f"{user}:{password}@{ipv4_address}:{port}"
        
        # Fix Scheme
        scheme = parsed.scheme.replace('postgres', 'postgresql')
        
        # Ensure SSL
        query = parsed.query
        if "sslmode" not in query:
            query += "&sslmode=require" if query else "sslmode=require"
            
        final_url = urlunparse((scheme, new_netloc, parsed.path, parsed.params, query, parsed.fragment))
        return final_url
        
    except Exception as e:
        # If this fails, we return the original URL but print the error to Vercel logs
        print(f"CRITICAL ERROR in DB URL Resolution: {e}")
        return raw_url.replace('postgres://', 'postgresql://')

# Apply the Safe URL
app.config['SQLALCHEMY_DATABASE_URI'] = get_safe_db_url()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 3. CONFIGURE FOR SUPABASE POOLER
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "poolclass": NullPool,
    "pool_pre_ping": True,
    "connect_args": {
        "connect_timeout": 10,
        "prepare_threshold": None 
    }
}

db = SQLAlchemy(app)

# --- MODELS ---
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

# --- ROUTES ---

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        if not username:
            flash("Username required")
            return redirect(url_for('login'))
        
        user = User.query.filter_by(username=username).first()
        if not user:
            # Safe IP extraction for Vercel
            user_ip = request.headers.get('x-forwarded-for', request.remote_addr)
            user = User(username=username, ip_address=user_ip)
            db.session.add(user)
            db.session.commit()
        else:
            user_ip = request.headers.get('x-forwarded-for', request.remote_addr)
            user.ip_address = user_ip
            db.session.commit()
        
        session['user_id'] = user.id
        session['username'] = user.username
        return redirect(url_for('dashboard'))
        
    return render_template('login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
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

@app.route('/setup_db')
def setup_db():
    try:
        db.create_all()
        if not City.query.first():
            us_cities = ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Jose", "Austin", "Jacksonville", "Fort Worth", "Columbus", "Charlotte", "San Francisco", "Indianapolis", "Seattle", "Denver", "Washington", "Boston", "El Paso", "Nashville", "Detroit", "Oklahoma City", "Portland", "Las Vegas", "Memphis", "Louisville", "Baltimore", "Milwaukee", "Albuquerque", "Tucson", "Fresno", "Mesa", "Sacramento", "Atlanta", "Kansas City", "Colorado Springs", "Miami", "Raleigh", "Omaha", "Long Beach", "Virginia Beach", "Oakland", "Minneapolis", "Tulsa", "Arlington", "Tampa", "New Orleans"]
            tr_cities = ["Istanbul", "Ankara", "Izmir", "Bursa", "Adana", "Gaziantep", "Konya", "Antalya", "Kayseri", "Mersin", "Eskisehir", "Diyarbakir", "Samsun", "Denizli", "Sanliurfa", "Malatya", "Kahramanmaras", "Erzurum", "Van", "Batman", "Elazig", "Izmit", "Manisa", "Sivas", "Gebze", "Balikesir", "Tarsus", "Trabzon", "Kutahya", "Corum", "Isparta", "Osmaniye", "Kirikkale", "Antakya", "Aydin", "Iskenderun", "Usak", "Aksaray", "Afyon", "Edirne"]
            for name in us_cities: db.session.add(City(name=name, country='USA', population=100000))
            for name in tr_cities: db.session.add(City(name=name, country='Turkiye', population=100000))
            db.session.commit()
            return "Database initialized and seeded!"
        return "Database already exists."
    except Exception as e:
        return f"Error: {str(e)}"