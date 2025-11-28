from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, case, and_
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_very_secret_key_fallback_12345')
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:<password>@localhost/digital_wallet'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 
login_manager.login_message_category = 'info' 

INCOME_CATEGORIES = ['Salary', 'Gift', 'Cash Deposit', 'Transfer', 'Others']
EXPENSE_CATEGORIES = ['Food & Drink', 'Shopping', 'Housing', 'Bills', 'Transport', 'Entertainment', 'Transfer', 'Others']
ALL_CATEGORIES = sorted(list(set(INCOME_CATEGORIES + EXPENSE_CATEGORIES)))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @property
    def balance(self):
        income = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == self.id,
            Transaction.type == 'Income'
        ).scalar() or Decimal('0.0')
        
        expense = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == self.id,
            Transaction.type == 'Expense'
        ).scalar() or Decimal('0.0')
        
        return income - expense

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    type = db.Column(db.String(20), nullable=False) # 'Income' or 'Expense'
    category = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    reference = db.Column(db.String(100), nullable=True)
    related_txn_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=True)

    user = db.relationship('User', backref=db.backref('transactions', lazy=True, cascade="all, delete-orphan"))

@login_manager.user_loader
def load_user(user_id):
    # This tells Flask-Login how to find a specific user from an ID
    return db.session.get(User, int(user_id))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            login_user(user, remember=request.form.get('remember'))
            flash('Logged in successfully!', 'success')
            return redirect(url_for('home'))
        else:
            flash('Login failed. Check email and password.', 'danger')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        if not username or not email or not password:
            flash('All fields are required.', 'danger')
            return redirect(url_for('register'))

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('You already have an account with that email. Please log in.', 'info')
            return redirect(url_for('login'))

        new_user = User(username=username, email=email)
        new_user.set_password(password)
        
        try:
            db.session.add(new_user)
            db.session.commit()
            flash(f'Account created for {username}! Please log in.', 'success')
            return redirect(url_for('login'))
        except IntegrityError:
            db.session.rollback()
            flash('Username already exists. Please choose another.', 'danger')
            return redirect(url_for('register'))
            
    return render_template('register.html')

@app.route('/logout')
@login_required 
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))


@app.route('/')
@login_required 
def home():
    cash_flow = db.session.query(
        func.sum(case((Transaction.type == 'Income', Transaction.amount), else_=0)).label('total_income'),
        func.sum(case((Transaction.type == 'Expense', Transaction.amount), else_=0)).label('total_expense')
    ).filter(Transaction.user_id == current_user.id).first()

    total_income = cash_flow.total_income or Decimal('0.0')
    total_expense = cash_flow.total_expense or Decimal('0.0')

    category_rows = db.session.query(
        Transaction.category,
        func.sum(Transaction.amount).label('total')
    ).filter(
        Transaction.user_id == current_user.id,
        Transaction.type == 'Expense'
    ).group_by(Transaction.category).order_by(func.sum(Transaction.amount).desc()).all()

    category_data = [[row.category, float(row.total)] for row in category_rows]

    recent_transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.timestamp.desc()).limit(5).all()

    return render_template('home.html',
        total_balance=current_user.balance,
        total_income=total_income,
        total_expense=total_expense,
        category_data=category_data, 
        recent_transactions=recent_transactions
    )

@app.route('/analytics')
@login_required
def analytics():
    now = datetime.utcnow()
    
    this_month_flow = db.session.query(
        func.sum(case((Transaction.type == 'Income', Transaction.amount), else_=0)).label('total_income'),
        func.sum(case((Transaction.type == 'Expense', Transaction.amount), else_=0)).label('total_expense')
    ).filter(
        Transaction.user_id == current_user.id,
        func.extract('month', Transaction.timestamp) == now.month,
        func.extract('year', Transaction.timestamp) == now.year
    ).first()
    
    this_month_income = this_month_flow.total_income or Decimal('0.0')
    this_month_expense = this_month_flow.total_expense or Decimal('0.0')
    this_month_total = this_month_income - this_month_expense

    days_in_month = (now.replace(month=now.month % 12 + 1, day=1) - timedelta(days=1)).day
    avg_daily_expense = this_month_expense / days_in_month if days_in_month > 0 else 0
    
    last_month = (now.replace(day=1) - timedelta(days=1))
    last_month_name = last_month.strftime('%B') 
    
    last_month_flow = db.session.query(
        func.sum(case((Transaction.type == 'Income', Transaction.amount), else_=0)).label('total_income'),
        func.sum(case((Transaction.type == 'Expense', Transaction.amount), else_=0)).label('total_expense')
    ).filter(
        Transaction.user_id == current_user.id,
        func.extract('month', Transaction.timestamp) == last_month.month,
        func.extract('year', Transaction.timestamp) == last_month.year
    ).first()
    
    last_month_total = (last_month_flow.total_income or 0) - (last_month_flow.total_expense or 0)
    
    expense_per_day = db.session.query(
        func.extract('day', Transaction.timestamp).label('day'),
        func.sum(Transaction.amount).label('total')
    ).filter(
        Transaction.user_id == current_user.id,
        Transaction.type == 'Expense',
        func.extract('month', Transaction.timestamp) == now.month,
        func.extract('year', Transaction.timestamp) == now.year
    ).group_by('day').order_by('day').all()
    
    chart_labels = [f"Day {int(d.day)}" for d in expense_per_day]
    chart_data = [float(d.total) for d in expense_per_day]

    return render_template('analytics.html',
        this_month_income=this_month_income,
        this_month_expense=this_month_expense,
        this_month_total=this_month_total,
        avg_daily_expense=avg_daily_expense,
        last_month_total=last_month_total,
        last_month_name=last_month_name, 
        chart_labels=chart_labels,
        chart_data=chart_data
    )

@app.route('/transactions')
@login_required
def transactions():
    transactions_by_date = db.session.query(
        func.date(Transaction.timestamp).label('date'),
        func.sum(case((Transaction.type == 'Income', Transaction.amount), else_=-Transaction.amount)).label('daily_total')
    ).filter(
        Transaction.user_id == current_user.id
    ).group_by(func.date(Transaction.timestamp)).order_by(func.date(Transaction.timestamp).desc()).all()

    all_transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.timestamp.desc()).all()
    
    grouped_txns = {}
    for date_total in transactions_by_date:
        date_str = date_total.date.strftime('%Y-%m-%d')
        grouped_txns[date_str] = {
            'daily_total': date_total.daily_total,
            'txns': []
        }
    
    for txn in all_transactions:
        date_str = txn.timestamp.strftime('%Y-%m-%d')
        if date_str in grouped_txns:
            grouped_txns[date_str]['txns'].append(txn)

    return render_template('transactions.html', grouped_txns=grouped_txns)


@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html') 
@app.route('/profile/delete', methods=['POST'])
@login_required
def delete_account():
    """Deletes the current user and all their data."""
    try:
        user_to_delete = db.session.get(User, current_user.id)
        if not user_to_delete:
            flash('User not found.', 'danger')
            return redirect(url_for('login'))
            
        logout_user()
        
        db.session.delete(user_to_delete)
        db.session.commit()
        
        flash('Your account and all associated data have been permanently deleted.', 'success')
        return redirect(url_for('register'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred while deleting your account: {e}', 'danger')
        return redirect(url_for('profile'))


@app.route('/transaction/add', methods=['GET', 'POST'])
@login_required
def add_transaction():
    all_users = User.query.filter(User.id != current_user.id).all()
    
    if request.method == 'POST':
        try:
            user_id = current_user.id
            amount = Decimal(request.form['amount'])
            txn_type = request.form['type']
            category = request.form['category']
            reference = request.form.get('reference') 

            if amount <= 0:
                flash('Amount must be positive.', 'danger')
                return redirect(url_for('add_transaction'))

            sender = db.session.get(User, user_id)
            
            # This check now happens *before* any logic, for all expense types
            if txn_type == 'Expense' and sender.balance < amount:
                flash(f'Insufficient balance. You only have ₹{sender.balance}.', 'danger')
                return redirect(url_for('add_transaction'))
            
            final_category = category
            if category == 'Others':
                custom_category = request.form.get('category_custom', '').strip()
                if not custom_category:
                    flash('Please enter a custom category name.', 'danger')
                    return redirect(url_for('add_transaction'))
                final_category = custom_category
            if category == 'Transfer': 
                
                receiver_id = int(request.form['receiver_id'])
                if user_id == receiver_id:
                    flash('Sender and receiver cannot be the same.', 'danger')
                    return redirect(url_for('add_transaction'))

                receiver = db.session.get(User, receiver_id)
                if not receiver:
                    flash('Receiver not found.', 'danger')
                    return redirect(url_for('add_transaction'))
                
                if sender.balance < amount:
                    flash(f'Insufficient balance.', 'danger')
                    return redirect(url_for('add_transaction'))

                expense_txn = Transaction(
                    user_id=sender.id, amount=amount, type='Expense',
                    category='Transfer', reference=f"To {receiver.username}: {reference}"
                )
                db.session.add(expense_txn)

                # 2. Income for Receiver
                income_txn = Transaction(
                    user_id=receiver.id, amount=amount, type='Income',
                    category='Transfer', reference=f"From {sender.username}: {reference}"
                )
                db.session.add(income_txn)
                
                # This links the two transactions so you can trace them
                db.session.flush() # flush to get IDs for the transactions
                expense_txn.related_txn_id = income_txn.id
                income_txn.related_txn_id = expense_txn.id

            # --- Normal Income/Expense Logic ---
            else:
                # Use final_category here
                new_txn = Transaction(
                    user_id=user_id, amount=amount, type=txn_type,
                    category=final_category, reference=reference
                )
                db.session.add(new_txn)
            
            db.session.commit()
            flash('Transaction added successfully!', 'success')
            return redirect(url_for('home'))

        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred: {e}', 'danger')
            return redirect(url_for('add_transaction'))

    return render_template('add_transaction.html', 
        all_users=all_users, 
        income_categories=INCOME_CATEGORIES, 
        expense_categories=EXPENSE_CATEGORIES
    )

# --- Utility Routes ---
@app.route('/api/categories')
@login_required
def api_categories():
    """Provides dynamic categories for the form"""
    txn_type = request.args.get('type')
    if txn_type == 'Income':
        return jsonify(INCOME_CATEGORIES)
    elif txn_type == 'Expense':
        return jsonify(EXPENSE_CATEGORIES)
    return jsonify([])

def format_datetime_filter(value, format_str):
    if value == 'now':
        ts = datetime.utcnow()
    elif isinstance(value, datetime):
        ts = value
    else:
        return value
    return ts.strftime(format_str)

app.jinja_env.filters['format_datetime'] = format_datetime_filter

@app.template_filter('format_date')
def format_date_filter(date_str):
    """Parses a YYYY-MM-DD string and formats it"""
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%A, %B %d')
    except ValueError:
        return date_str

# --- Main Runner ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all() # Creates tables based on models if they don't exist
    app.run(debug=True) # Runs the app in debug mode
