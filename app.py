import os
import sys
import secrets
import threading
import time
import random
import requests
import json
import subprocess
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, render_template, request, redirect, url_for, session, send_file, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
import pandas as pd
from io import BytesIO
from binance.client import Client
from binance.exceptions import BinanceAPIException
import ta
from cryptography.fernet import Fernet
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from dotenv import load_dotenv
from flask_migrate import Migrate

# ==================== CHARGEMENT DU .env ====================
load_dotenv()
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_SECRET')
SECRET_KEY = os.getenv('SECRET_KEY', 'votre_cle_secrete_changez_moi_123456')

# ==================== PARAMÈTRES DE TRADING ====================
INVEST_AMOUNT_USDT = float(os.getenv('INVEST_AMOUNT_USDT', 5.0))
LEVERAGE = int(os.getenv('LEVERAGE', 5))
MAX_POSITIONS = int(os.getenv('MAX_POSITIONS', 2))

# ==================== VÉRIFICATION DES CLÉS ====================
print("🔑 Vérification des clés API :")
print(f"   API_KEY : {API_KEY[:10] if API_KEY else '❌ MANQUANTE'}...")
print(f"   API_SECRET : {API_SECRET[:10] if API_SECRET else '❌ MANQUANTE'}...")
if not API_KEY or not API_SECRET:
    print("⚠️ ATTENTION : Les clés API ne sont pas chargées depuis le fichier .env !")
else:
    print("✅ Clés API chargées avec succès.")

# ==================== CONFIGURATION ====================
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inginho_final.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ==================== CHIFFREMENT ====================
KEY_FILE = 'secret.key'
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, 'wb') as f:
        f.write(Fernet.generate_key())
with open(KEY_FILE, 'rb') as f:
    cipher_key = f.read()
cipher = Fernet(cipher_key)

def encrypt_api_key(api_key):
    if not api_key:
        return None
    return cipher.encrypt(api_key.encode()).decode()

def decrypt_api_key(encrypted_key):
    if not encrypted_key:
        return None
    try:
        return cipher.decrypt(encrypted_key.encode()).decode()
    except:
        return None

# ==================== COMMISSIONS ====================
COMMISSIONS = {
    'Trial': 0.0,
    'Standard': 0.20,
    'Pro': 0.15,
    'Premium Pro': 0.10
}

# ==================== CUSTOM CLIENT BINANCE ====================
class CustomClient(Client):
    def _get_timestamp(self):
        try:
            return self._get_server_time()
        except:
            return int((time.time() - 2) * 1000)

    def _request(self, method, uri, signed=False, **kwargs):
        if signed:
            kwargs['recvWindow'] = 60000      # 60 secondes de tolérance
            kwargs['timestamp'] = self._get_timestamp()
        return super()._request(method, uri, signed, **kwargs)
    
    # AJOUT : forcer le recvWindow pour éviter l'erreur -1021
    def _request(self, method, uri, signed=False, **kwargs):
        if signed:
            kwargs['recvWindow'] = 60000  # 60 secondes de tolérance
            kwargs['timestamp'] = self._get_timestamp()
        return super()._request(method, uri, signed, **kwargs)

# ==================== VERSION ET MISE À JOUR ====================
CURRENT_VERSION = "2.0.0"
UPDATE_URL = "https://raw.githubusercontent.com/votre-repo/inginho/version.json"

def check_update():
    try:
        response = requests.get(UPDATE_URL, timeout=5)
        if response.status_code == 200:
            data = response.json()
            latest_version = data.get('version', '1.0.0')
            if latest_version > CURRENT_VERSION:
                print(f"🔄 Nouvelle version disponible : {latest_version}")
                return True, latest_version
    except:
        print("⚠️ Impossible de vérifier les mises à jour.")
    return False, CURRENT_VERSION

def perform_update():
    try:
        response = requests.get("https://raw.githubusercontent.com/votre-repo/inginho/main/app.py")
        if response.status_code == 200:
            with open('app.py.new', 'w', encoding='utf-8') as f:
                f.write(response.text)
            print("✅ Fichier de mise à jour téléchargé.")
            return True
    except:
        print("❌ Échec du téléchargement de la mise à jour.")
    return False

# ==================== MODÈLES ====================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100), nullable=True)
    language = db.Column(db.String(5), default='en')
    is_admin = db.Column(db.Boolean, default=False)
    api_key_binance = db.Column(db.Text, nullable=True)
    api_secret_binance = db.Column(db.Text, nullable=True)
    trading_mode = db.Column(db.String(10), default='futures')
    auto_scan = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    duration_months = db.Column(db.Integer)
    price_usd = db.Column(db.Float)
    max_bots = db.Column(db.Integer)
    commission_rate = db.Column(db.Float)
    features = db.Column(db.Text)

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    plan_id = db.Column(db.Integer, db.ForeignKey('plan.id'))
    activation_code = db.Column(db.String(50), unique=True)
    start_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    end_date = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    user = db.relationship('User', backref='subscriptions')
    plan = db.relationship('Plan')

class Wallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True)
    balance = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(10), default='USDT')
    user = db.relationship('User', backref='wallet')

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'))
    amount = db.Column(db.Float)
    type = db.Column(db.String(30))
    description = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    wallet = db.relationship('Wallet', backref='transactions')

class BotInstance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100))
    symbol = db.Column(db.String(20))
    timeframe = db.Column(db.String(10), default='5m')
    sl_percent = db.Column(db.Float, default=2.0)
    tp_percent = db.Column(db.Float, default=4.0)
    strategy = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    profit = db.Column(db.Float, default=0.0)
    entry_price = db.Column(db.Float, default=0.0)
    position_qty = db.Column(db.Float, default=0.0)
    signal_text = db.Column(db.String(50), default='⏳ WAIT')
    signal_color = db.Column(db.String(20), default='secondary')
    rsi_value = db.Column(db.String(10), default='N/A')
    user = db.relationship('User', backref='bots')

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    title = db.Column(db.String(100))
    message = db.Column(db.Text)
    type = db.Column(db.String(20))
    is_auto = db.Column(db.Boolean, default=True)
    is_sent = db.Column(db.Boolean, default=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', backref='notifications')

class FAQ(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    keywords = db.Column(db.String(200))
    response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class ClientQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    question = db.Column(db.Text)
    answer = db.Column(db.Text, nullable=True)
    answered_by = db.Column(db.String(50), default='auto')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    answered_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User', backref='questions')

# ==================== MIGRATION AUTOMATIQUE ====================
def migrate_db():
    with app.app_context():
        import sqlite3
        conn = sqlite3.connect('inginho_final.db')
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user'")
        if not cursor.fetchone():
            conn.close()
            return
        cursor.execute("PRAGMA table_info(user)")
        existing_cols = [col[1] for col in cursor.fetchall()]
        if 'trading_mode' not in existing_cols:
            cursor.execute("ALTER TABLE user ADD COLUMN trading_mode TEXT DEFAULT 'futures'")
            print("✅ Colonne trading_mode ajoutée")
        if 'auto_scan' not in existing_cols:
            cursor.execute("ALTER TABLE user ADD COLUMN auto_scan BOOLEAN DEFAULT 0")
            print("✅ Colonne auto_scan ajoutée")
        if 'created_at' not in existing_cols:
            cursor.execute("ALTER TABLE user ADD COLUMN created_at DATETIME")
            print("✅ Colonne created_at ajoutée")
        conn.commit()
        conn.close()

# ==================== LANGUES ====================
LANGUAGES = {
    "en": {"name": "English", "app_title": "IN-GINHO Ai Studio Pro", "login": "Login", "register": "Register", "logout": "Logout", "dashboard": "Dashboard", "bots": "My Bots", "wallet": "Wallet", "subscription": "Subscription", "admin": "Admin Panel", "email": "Email", "password": "Password", "username": "Username", "language": "Language", "welcome": "Welcome", "balance": "Balance", "total_profit": "Total P&L", "active_bots": "Active Bots", "plan": "Plan", "expires": "Expires", "withdraw": "Withdraw", "amount": "Amount", "submit": "Submit", "generate_code": "Generate Code", "users": "Users", "export_excel": "Export to Excel", "commission_rate": "Commission Rate", "current_plan": "Current Plan", "upgrade": "Upgrade", "trial": "Trial", "standard": "Standard", "pro": "Pro", "premium": "Premium Pro", "duration": "Duration (months)", "price": "Price (USD)", "buy": "Buy / Activate", "activation_code": "Activation Code", "enter_code": "Enter your code", "activate": "Activate", "success": "Success", "error": "Error", "admin_only": "Admin area", "my_bots": "My Bots", "create_bot": "Create Bot", "symbol": "Symbol", "strategy": "Strategy", "start": "Start", "stop": "Stop", "transactions": "Transactions", "settings": "Settings", "api_keys": "API Keys", "profile": "Profile", "clients": "Clients", "support": "Support", "trading_mode": "Trading Mode"},
    "fr": {"name": "Français", "app_title": "IN-GINHO Ai Studio Pro", "login": "Connexion", "register": "Inscription", "logout": "Déconnexion", "dashboard": "Tableau de bord", "bots": "Mes Bots", "wallet": "Portefeuille", "subscription": "Abonnement", "admin": "Admin", "email": "Email", "password": "Mot de passe", "username": "Nom d'utilisateur", "language": "Langue", "welcome": "Bienvenue", "balance": "Solde", "total_profit": "P&L total", "active_bots": "Bots actifs", "plan": "Plan", "expires": "Expire le", "withdraw": "Retirer", "amount": "Montant", "submit": "Envoyer", "generate_code": "Générer un code", "users": "Utilisateurs", "export_excel": "Exporter en Excel", "commission_rate": "Taux de commission", "current_plan": "Plan actuel", "upgrade": "Mettre à niveau", "trial": "Essai", "standard": "Standard", "pro": "Pro", "premium": "Premium Pro", "duration": "Durée (mois)", "price": "Prix (USD)", "buy": "Acheter / Activer", "activation_code": "Code d'activation", "enter_code": "Entrez votre code", "activate": "Activer", "success": "Succès", "error": "Erreur", "admin_only": "Zone admin", "my_bots": "Mes Bots", "create_bot": "Créer un Bot", "symbol": "Symbole", "strategy": "Stratégie", "start": "Démarrer", "stop": "Arrêter", "transactions": "Transactions", "settings": "Paramètres", "api_keys": "Clés API", "profile": "Profil", "clients": "Clients", "support": "Support", "trading_mode": "Mode de trading"},
    "hi": {"name": "हिन्दी", "app_title": "IN-GINHO Ai Studio Pro", "login": "लॉगिन", "register": "पंजीकरण", "logout": "लॉगआउट", "dashboard": "डैशबोर्ड", "bots": "मेरे बॉट", "wallet": "वॉलेट", "subscription": "सदस्यता", "admin": "एडमिन", "email": "ईमेल", "password": "पासवर्ड", "username": "उपयोगकर्ता नाम", "language": "भाषा", "welcome": "स्वागत है", "balance": "शेष राशि", "total_profit": "कुल लाभ/हानि", "active_bots": "सक्रिय बॉट", "plan": "योजना", "expires": "समाप्ति", "withdraw": "निकासी", "amount": "राशि", "submit": "जमा करें", "generate_code": "कोड जनरेट करें", "users": "उपयोगकर्ता", "export_excel": "एक्सेल में निर्यात करें", "commission_rate": "कमीशन दर", "current_plan": "वर्तमान योजना", "upgrade": "अपग्रेड", "trial": "ट्रायल", "standard": "मानक", "pro": "प्रो", "premium": "प्रीमियम प्रो", "duration": "अवधि (महीने)", "price": "कीमत (USD)", "buy": "खरीदें / सक्रिय करें", "activation_code": "सक्रियण कोड", "enter_code": "अपना कोड दर्ज करें", "activate": "सक्रिय करें", "success": "सफलता", "error": "त्रुटि", "admin_only": "एडमिन क्षेत्र", "my_bots": "मेरे बॉट", "create_bot": "बॉट बनाएं", "symbol": "प्रतीक", "strategy": "रणनीति", "start": "शुरू करें", "stop": "रोकें", "transactions": "लेन-देन", "settings": "सेटिंग्स", "api_keys": "एपीआई कुंजी", "profile": "प्रोफ़ाइल", "clients": "ग्राहक", "support": "सहायता", "trading_mode": "ट्रेडिंग मोड"},
    "pt": {"name": "Português", "app_title": "IN-GINHO Ai Studio Pro", "login": "Entrar", "register": "Registrar", "logout": "Sair", "dashboard": "Painel", "bots": "Meus Bots", "wallet": "Carteira", "subscription": "Assinatura", "admin": "Admin", "email": "Email", "password": "Senha", "username": "Usuário", "language": "Idioma", "welcome": "Bem-vindo", "balance": "Saldo", "total_profit": "P&L Total", "active_bots": "Bots Ativos", "plan": "Plano", "expires": "Expira em", "withdraw": "Sacar", "amount": "Valor", "submit": "Enviar", "generate_code": "Gerar Código", "users": "Usuários", "export_excel": "Exportar para Excel", "commission_rate": "Taxa de Comissão", "current_plan": "Plano Atual", "upgrade": "Atualizar", "trial": "Teste", "standard": "Padrão", "pro": "Pro", "premium": "Premium Pro", "duration": "Duração (meses)", "price": "Preço (USD)", "buy": "Comprar / Ativar", "activation_code": "Código de Ativação", "enter_code": "Digite seu código", "activate": "Ativar", "success": "Sucesso", "error": "Erro", "admin_only": "Área admin", "my_bots": "Meus Bots", "create_bot": "Criar Bot", "symbol": "Símbolo", "strategy": "Estratégia", "start": "Iniciar", "stop": "Parar", "transactions": "Transações", "settings": "Configurações", "api_keys": "Chaves API", "profile": "Perfil", "clients": "Clientes", "support": "Suporte", "trading_mode": "Modo de Trading"},
    "zh": {"name": "中文", "app_title": "IN-GINHO Ai Studio Pro", "login": "登录", "register": "注册", "logout": "退出", "dashboard": "仪表板", "bots": "我的机器人", "wallet": "钱包", "subscription": "订阅", "admin": "管理员", "email": "电子邮件", "password": "密码", "username": "用户名", "language": "语言", "welcome": "欢迎", "balance": "余额", "total_profit": "总盈亏", "active_bots": "活跃机器人", "plan": "计划", "expires": "到期时间", "withdraw": "提现", "amount": "金额", "submit": "提交", "generate_code": "生成代码", "users": "用户", "export_excel": "导出Excel", "commission_rate": "佣金率", "current_plan": "当前计划", "upgrade": "升级", "trial": "试用", "standard": "标准", "pro": "专业", "premium": "高级专业", "duration": "期限(月)", "price": "价格(USD)", "buy": "购买/激活", "activation_code": "激活码", "enter_code": "输入您的代码", "activate": "激活", "success": "成功", "error": "错误", "admin_only": "管理员区域", "my_bots": "我的机器人", "create_bot": "创建机器人", "symbol": "交易对", "strategy": "策略", "start": "启动", "stop": "停止", "transactions": "交易记录", "settings": "设置", "api_keys": "API密钥", "profile": "个人资料", "clients": "客户", "support": "支持", "trading_mode": "交易模式"}
}

def t(key, lang='en', **kwargs):
    text = LANGUAGES.get(lang, LANGUAGES['en']).get(key, key)
    return text.format(**kwargs) if kwargs else text

# ==================== DECORATEURS ====================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = db.session.get(User, session['user_id'])
        if not user or not user.is_admin:
            return "Accès non autorisé", 403
        return f(*args, **kwargs)
    return decorated

# ==================== INIT DB ====================
def init_db():
    with app.app_context():
        db.create_all()
        migrate_db()
        
        if Plan.query.count() == 0:
            plans = [
                Plan(name='Trial', duration_months=0, price_usd=0.0, max_bots=1, commission_rate=0.0, features='1 bot'),
                Plan(name='Standard', duration_months=1, price_usd=29.0, max_bots=3, commission_rate=COMMISSIONS['Standard'], features='3 bots'),
                Plan(name='Standard', duration_months=3, price_usd=79.0, max_bots=3, commission_rate=COMMISSIONS['Standard'], features='3 bots'),
                Plan(name='Standard', duration_months=6, price_usd=149.0, max_bots=3, commission_rate=COMMISSIONS['Standard'], features='3 bots'),
                Plan(name='Standard', duration_months=12, price_usd=279.0, max_bots=3, commission_rate=COMMISSIONS['Standard'], features='3 bots'),
                Plan(name='Pro', duration_months=1, price_usd=59.0, max_bots=5, commission_rate=COMMISSIONS['Pro'], features='5 bots'),
                Plan(name='Pro', duration_months=3, price_usd=159.0, max_bots=5, commission_rate=COMMISSIONS['Pro'], features='5 bots'),
                Plan(name='Pro', duration_months=6, price_usd=299.0, max_bots=5, commission_rate=COMMISSIONS['Pro'], features='5 bots'),
                Plan(name='Pro', duration_months=12, price_usd=559.0, max_bots=5, commission_rate=COMMISSIONS['Pro'], features='5 bots'),
                Plan(name='Premium Pro', duration_months=1, price_usd=99.0, max_bots=10, commission_rate=COMMISSIONS['Premium Pro'], features='10 bots'),
                Plan(name='Premium Pro', duration_months=3, price_usd=269.0, max_bots=10, commission_rate=COMMISSIONS['Premium Pro'], features='10 bots'),
                Plan(name='Premium Pro', duration_months=6, price_usd=499.0, max_bots=10, commission_rate=COMMISSIONS['Premium Pro'], features='10 bots'),
                Plan(name='Premium Pro', duration_months=12, price_usd=899.0, max_bots=10, commission_rate=COMMISSIONS['Premium Pro'], features='10 bots'),
            ]
            db.session.add_all(plans)
            db.session.commit()
        if not User.query.filter_by(email='admin@admin.com').first():
            admin = User(username='admin', email='admin@admin.com', password='admin123', is_admin=True, language='en', full_name='Administrateur', trading_mode='futures')
            db.session.add(admin)
            db.session.commit()
            wallet = Wallet(user_id=admin.id, balance=1000.0)
            db.session.add(wallet)
            db.session.commit()
        if FAQ.query.count() == 0:
            faqs = [
                FAQ(keywords='activer, code, activation', response='Pour activer votre abonnement, allez dans l\'onglet "Abonnement", entrez le code d\'activation reçu par email et cliquez sur "Activer".'),
                FAQ(keywords='bot, démarre, fonctionne', response='Pour démarrer un bot, allez dans "Mes Bots", créez un bot, puis cliquez sur le bouton "Start" (vert) à côté de son nom.'),
                FAQ(keywords='clé, api, binance', response='Pour ajouter vos clés API Binance, allez dans "Paramètres", entrez votre API Key et Secret, puis cliquez sur "Enregistrer". Vos clés sont chiffrées pour votre sécurité.'),
                FAQ(keywords='commission, gagner, argent', response='Vous gagnez des commissions sur les gains de vos clients. Le pourcentage dépend de votre plan (Standard:20%, Pro:15%, Premium:10%). Les commissions sont automatiquement créditées sur votre portefeuille interne.'),
                FAQ(keywords='wallet, solde, retrait', response='Votre solde est visible dans l\'onglet "Portefeuille". Vous pouvez retirer vos fonds en cliquant sur "Retirer" et en entrant le montant souhaité.'),
                FAQ(keywords='plan, abonnement, durée', response='Les abonnements sont disponibles en durées de 1, 3, 6 ou 12 mois. Vous pouvez choisir le plan qui vous convient dans l\'onglet "Abonnement".'),
                FAQ(keywords='support, contact, aide', response='Pour toute question, vous pouvez utiliser ce formulaire de support. Un administrateur vous répondra dans les plus brefs délais.'),
                FAQ(keywords='mode, trading, spot, futures, hedge', response='Vous pouvez choisir votre mode de trading dans l\'onglet "Paramètres" : Futures (levier), Spot (achat/vente simple), Hedge (Long et Short simultanés).'),
            ]
            db.session.add_all(faqs)
            db.session.commit()
init_db()

# ==================== STRATEGIE ====================
def get_signal(df):
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    macd = ta.trend.MACD(df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
    df['bb_mid'] = bb.bollinger_mavg()
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    rsi_bull = latest['rsi'] < 45
    macd_bull = latest['macd'] > latest['macd_signal'] and prev['macd'] <= prev['macd_signal']
    bb_bull = latest['close'] > latest['bb_mid']
    rsi_bear = latest['rsi'] > 55
    macd_bear = latest['macd'] < latest['macd_signal'] and prev['macd'] >= prev['macd_signal']
    bb_bear = latest['close'] < latest['bb_mid']
    if rsi_bull and macd_bull and bb_bull:
        return "BUY", latest['close'], latest['rsi'], "Bullish"
    elif rsi_bear and macd_bear and bb_bear:
        return "SELL", latest['close'], latest['rsi'], "Bearish"
    else:
        return "HOLD", latest['close'], latest['rsi'], "Neutral"

# ==================== SCAN DES PAIRES (DYNAMIQUE) ====================
def scan_pairs(api_key, api_secret):
    client = CustomClient(api_key, api_secret)
    try:
        exchange_info = client.futures_exchange_info()
        pairs = []
        for s in exchange_info['symbols']:
            if s['symbol'].endswith('USDT') and s['status'] == 'TRADING':
                pairs.append(s['symbol'])
        print(f"ℹ️ {len(pairs)} paires Futures chargées depuis Binance")
    except Exception as e:
        print(f"⚠️ Erreur chargement dynamique: {e} -> utilisation d'une liste de secours")
        pairs = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT']
    
    results = []
    total_pairs = len(pairs)
    for idx, symbol in enumerate(pairs, 1):
        try:
            print(f"📡 Scan {symbol} ({idx}/{total_pairs})...", end='\r')
            klines = client.futures_klines(symbol=symbol, interval='5m', limit=100)
            if not klines:
                continue
            df = pd.DataFrame(klines)
            df.columns = ['time','open','high','low','close','volume','close_time',
                         'quote_asset_volume','number_of_trades','taker_buy_base_asset_volume',
                         'taker_buy_quote_asset_volume','ignore']
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
            if df['close'].isnull().all():
                continue
            rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            macd = ta.trend.MACD(df['close'])
            bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
            last_close = df['close'].iloc[-1]
            last_volume = df['volume'].iloc[-1]
            last_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
            bb_mid = bb.bollinger_mavg().iloc[-1] if not pd.isna(bb.bollinger_mavg().iloc[-1]) else last_close
            score = 0
            if last_rsi < 35: score += 2
            if last_rsi > 65: score += 2
            if last_close > bb_mid: score += 1
            if last_close < bb_mid: score += 1
            if last_volume > df['volume'].mean() * 1.5: score += 1
            if last_rsi < 35:
                signal, sentiment, color = "BUY", "Acheter", "success"
            elif last_rsi > 65:
                signal, sentiment, color = "SELL", "Vendre", "danger"
            else:
                signal, sentiment, color = "HOLD", "Attendre", "secondary"
            results.append({
                'symbol': symbol,
                'rsi': round(last_rsi, 1),
                'price': round(last_close, 2),
                'volume': round(last_volume, 2),
                'score': score,
                'signal': signal,
                'sentiment': sentiment,
                'color': color
            })
        except Exception as e:
            continue
    results.sort(key=lambda x: x['score'], reverse=True)
    print(f"\n✅ Scan terminé : {len(results)}/{total_pairs} paires analysées")
    return results[:5]

# ==================== BOT SIMULATEUR ====================
def bot_simulator():
    with app.app_context():
        while True:
            time.sleep(10)
            try:
                bots = BotInstance.query.filter_by(is_active=True).all()
                for bot in bots:
                    try:
                        user = db.session.get(User, bot.user_id)
                        if not user:
                            continue
                        
                        positions_ouvertes = 0
                        for b in BotInstance.query.filter_by(user_id=bot.user_id, is_active=True).all():
                            if b.entry_price > 0:
                                positions_ouvertes += 1
                        
                        if positions_ouvertes >= MAX_POSITIONS:
                            continue
                        
                        api_key = API_KEY if API_KEY else decrypt_api_key(user.api_key_binance)
                        api_secret = API_SECRET if API_SECRET else decrypt_api_key(user.api_secret_binance)
                        
                        if not api_key or not api_secret:
                            pct_change = random.uniform(-0.05, 0.08)
                            profit = bot.profit * pct_change if bot.profit != 0 else random.uniform(-10, 20)
                            bot.profit += profit
                            if profit > 0:
                                sub = Subscription.query.filter_by(user_id=bot.user_id, is_active=True).first()
                                if sub and sub.plan:
                                    rate = sub.plan.commission_rate
                                    commission = profit * rate
                                    admin_wallet = Wallet.query.filter_by(user_id=1).first()
                                    if admin_wallet:
                                        admin_wallet.balance += commission
                                        tx = Transaction(wallet_id=admin_wallet.id, amount=commission, type='commission', description=f'Commission simulée {bot.name}')
                                        db.session.add(tx)
                                    client_wallet = Wallet.query.filter_by(user_id=bot.user_id).first()
                                    if client_wallet:
                                        client_wallet.balance += (profit - commission)
                                        tx2 = Transaction(wallet_id=client_wallet.id, amount=(profit - commission), type='deposit', description=f'Profit simulé {bot.name}')
                                        db.session.add(tx2)
                            db.session.commit()
                            continue
                        
                        try:
                            client = CustomClient(api_key, api_secret)
                            klines = client.futures_klines(symbol=bot.symbol, interval=bot.timeframe, limit=100)
                            df = pd.DataFrame(klines)
                            df.columns = ['time','open','high','low','close','volume','close_time','quote_asset_volume','number_of_trades','taker_buy_base_asset_volume','taker_buy_quote_asset_volume','ignore']
                            df['close'] = pd.to_numeric(df['close'])
                            df['open'] = pd.to_numeric(df['open'])
                            df['high'] = pd.to_numeric(df['high'])
                            df['low'] = pd.to_numeric(df['low'])
                            
                            signal, price, rsi, sentiment = get_signal(df)
                            
                            if signal == "BUY":
                                bot.signal_text = f"🔥 BUY (LONG)"
                                bot.signal_color = "success"
                            elif signal == "SELL":
                                bot.signal_text = f"📉 SELL (SHORT)"
                                bot.signal_color = "danger"
                            else:
                                bot.signal_text = f"⏳ WAIT / HOLD"
                                bot.signal_color = "secondary"
                            bot.rsi_value = f"{rsi:.1f}"
                            db.session.commit()
                            
                            print(f"📊 {bot.name} ({bot.symbol}) Signal: {signal} | Prix: {price} | RSI: {rsi:.1f}")
                            
                            trading_mode = user.trading_mode if hasattr(user, 'trading_mode') else 'futures'
                            leverage = LEVERAGE
                            
                            invest_amount = INVEST_AMOUNT_USDT
                            try:
                                ticker = client.futures_symbol_ticker(symbol=bot.symbol)
                                current_price = float(ticker['price'])
                                raw_qty = invest_amount / current_price
                                qty = round(raw_qty, 5)
                                if qty < 0.001:
                                    qty = 0.001
                                if qty > 100:
                                    qty = 100
                                print(f"📊 {bot.symbol}: invest {invest_amount}$ @ {current_price} => qty {qty}")
                            except Exception as e:
                                print(f"⚠️ Erreur calcul quantité {bot.symbol}: {e}")
                                qty = 0.001

                            if trading_mode == 'spot':
                                if signal == "BUY":
                                    order = client.order_market_buy(symbol=bot.symbol, quantity=qty)
                                    entry_price = float(order['fills'][0]['price'])
                                    bot.entry_price = entry_price
                                    bot.position_qty = qty
                                    db.session.commit()
                                    print(f"✅ BUY SPOT {bot.symbol} @ {entry_price}")
                                elif signal == "SELL" and bot.position_qty > 0:
                                    order = client.order_market_sell(symbol=bot.symbol, quantity=bot.position_qty)
                                    exit_price = float(order['fills'][0]['price'])
                                    profit = (exit_price - bot.entry_price) * bot.position_qty
                                    bot.profit += profit
                                    bot.entry_price = 0
                                    bot.position_qty = 0
                                    db.session.commit()
                                    print(f"✅ SELL SPOT {bot.symbol} @ {exit_price} | Profit: {profit:.2f}")
                                
                            elif trading_mode == 'hedge':
                                positions = client.futures_position_information(symbol=bot.symbol)
                                long_qty = 0
                                short_qty = 0
                                for pos in positions:
                                    if float(pos['positionAmt']) > 0:
                                        long_qty = float(pos['positionAmt'])
                                    elif float(pos['positionAmt']) < 0:
                                        short_qty = abs(float(pos['positionAmt']))
                                
                                if signal == "BUY" and long_qty == 0:
                                    client.futures_change_leverage(symbol=bot.symbol, leverage=leverage)
                                    order = client.futures_create_order(symbol=bot.symbol, side='BUY', type='MARKET', quantity=qty)
                                    entry_price = float(order['fills'][0]['price'])
                                    sl_price = round(entry_price * (1 - bot.sl_percent / 100), 8)
                                    tp_price = round(entry_price * (1 + bot.tp_percent / 100), 8)
                                    client.futures_create_order(symbol=bot.symbol, side='SELL', type='STOP_MARKET', stopPrice=sl_price, quantity=qty, reduceOnly=True)
                                    client.futures_create_order(symbol=bot.symbol, side='SELL', type='TAKE_PROFIT_MARKET', stopPrice=tp_price, quantity=qty, reduceOnly=True)
                                    bot.entry_price = entry_price
                                    bot.position_qty = qty
                                    db.session.commit()
                                    print(f"✅ HEDGE LONG {bot.symbol} @ {entry_price}")
                                    
                                elif signal == "SELL" and short_qty == 0:
                                    client.futures_change_leverage(symbol=bot.symbol, leverage=leverage)
                                    order = client.futures_create_order(symbol=bot.symbol, side='SELL', type='MARKET', quantity=qty)
                                    entry_price = float(order['fills'][0]['price'])
                                    sl_price = round(entry_price * (1 + bot.sl_percent / 100), 8)
                                    tp_price = round(entry_price * (1 - bot.tp_percent / 100), 8)
                                    client.futures_create_order(symbol=bot.symbol, side='BUY', type='STOP_MARKET', stopPrice=sl_price, quantity=qty, reduceOnly=True)
                                    client.futures_create_order(symbol=bot.symbol, side='BUY', type='TAKE_PROFIT_MARKET', stopPrice=tp_price, quantity=qty, reduceOnly=True)
                                    bot.entry_price = entry_price
                                    bot.position_qty = -qty
                                    db.session.commit()
                                    print(f"✅ HEDGE SHORT {bot.symbol} @ {entry_price}")
                                
                                if long_qty == 0 and bot.position_qty > 0:
                                    ticker = client.futures_symbol_ticker(symbol=bot.symbol)
                                    current_price = float(ticker['price'])
                                    profit = (current_price - bot.entry_price) * bot.position_qty
                                    if profit > 0:
                                        sub = Subscription.query.filter_by(user_id=bot.user_id, is_active=True).first()
                                        if sub and sub.plan:
                                            rate = sub.plan.commission_rate
                                            commission = profit * rate
                                            admin_wallet = Wallet.query.filter_by(user_id=1).first()
                                            if admin_wallet:
                                                admin_wallet.balance += commission
                                                tx = Transaction(wallet_id=admin_wallet.id, amount=commission, type='commission', description=f'Commission Hedge Long {bot.name}')
                                                db.session.add(tx)
                                            client_wallet = Wallet.query.filter_by(user_id=bot.user_id).first()
                                            if client_wallet:
                                                client_wallet.balance += (profit - commission)
                                                tx2 = Transaction(wallet_id=client_wallet.id, amount=(profit - commission), type='deposit', description=f'Profit Hedge Long {bot.name}')
                                                db.session.add(tx2)
                                    bot.profit += profit
                                    bot.entry_price = 0
                                    bot.position_qty = 0
                                    db.session.commit()
                                    print(f"💰 HEDGE LONG fermé pour {bot.name}, Profit: {profit:.2f}")
                                
                                if short_qty == 0 and bot.position_qty < 0:
                                    ticker = client.futures_symbol_ticker(symbol=bot.symbol)
                                    current_price = float(ticker['price'])
                                    profit = (bot.entry_price - current_price) * abs(bot.position_qty)
                                    if profit > 0:
                                        sub = Subscription.query.filter_by(user_id=bot.user_id, is_active=True).first()
                                        if sub and sub.plan:
                                            rate = sub.plan.commission_rate
                                            commission = profit * rate
                                            admin_wallet = Wallet.query.filter_by(user_id=1).first()
                                            if admin_wallet:
                                                admin_wallet.balance += commission
                                                tx = Transaction(wallet_id=admin_wallet.id, amount=commission, type='commission', description=f'Commission Hedge Short {bot.name}')
                                                db.session.add(tx)
                                            client_wallet = Wallet.query.filter_by(user_id=bot.user_id).first()
                                            if client_wallet:
                                                client_wallet.balance += (profit - commission)
                                                tx2 = Transaction(wallet_id=client_wallet.id, amount=(profit - commission), type='deposit', description=f'Profit Hedge Short {bot.name}')
                                                db.session.add(tx2)
                                    bot.profit += profit
                                    bot.entry_price = 0
                                    bot.position_qty = 0
                                    db.session.commit()
                                    print(f"💰 HEDGE SHORT fermé pour {bot.name}, Profit: {profit:.2f}")
                                
                            else:
                                positions = client.futures_position_information(symbol=bot.symbol)
                                current_pos_qty = 0
                                for pos in positions:
                                    if float(pos['positionAmt']) != 0:
                                        current_pos_qty = float(pos['positionAmt'])
                                        break
                                
                                if signal == "BUY" and current_pos_qty == 0 and bot.position_qty == 0:
                                    client.futures_change_leverage(symbol=bot.symbol, leverage=leverage)
                                    order = client.futures_create_order(symbol=bot.symbol, side='BUY', type='MARKET', quantity=qty)
                                    entry_price = float(order['fills'][0]['price'])
                                    sl_price = round(entry_price * (1 - bot.sl_percent / 100), 8)
                                    tp_price = round(entry_price * (1 + bot.tp_percent / 100), 8)
                                    client.futures_create_order(symbol=bot.symbol, side='SELL', type='STOP_MARKET', stopPrice=sl_price, quantity=qty, reduceOnly=True)
                                    client.futures_create_order(symbol=bot.symbol, side='SELL', type='TAKE_PROFIT_MARKET', stopPrice=tp_price, quantity=qty, reduceOnly=True)
                                    bot.entry_price = entry_price
                                    bot.position_qty = qty
                                    db.session.commit()
                                    print(f"✅ BUY FUTURES {bot.symbol} @ {entry_price} (SL: {sl_price}, TP: {tp_price}, Qty: {qty})")
                                    
                                elif signal == "SELL" and current_pos_qty == 0 and bot.position_qty == 0:
                                    client.futures_change_leverage(symbol=bot.symbol, leverage=leverage)
                                    order = client.futures_create_order(symbol=bot.symbol, side='SELL', type='MARKET', quantity=qty)
                                    entry_price = float(order['fills'][0]['price'])
                                    sl_price = round(entry_price * (1 + bot.sl_percent / 100), 8)
                                    tp_price = round(entry_price * (1 - bot.tp_percent / 100), 8)
                                    client.futures_create_order(symbol=bot.symbol, side='BUY', type='STOP_MARKET', stopPrice=sl_price, quantity=qty, reduceOnly=True)
                                    client.futures_create_order(symbol=bot.symbol, side='BUY', type='TAKE_PROFIT_MARKET', stopPrice=tp_price, quantity=qty, reduceOnly=True)
                                    bot.entry_price = entry_price
                                    bot.position_qty = -qty
                                    db.session.commit()
                                    print(f"✅ SELL FUTURES {bot.symbol} @ {entry_price} (SL: {sl_price}, TP: {tp_price}, Qty: {qty})")
                                
                                if current_pos_qty == 0 and bot.position_qty != 0:
                                    ticker = client.futures_symbol_ticker(symbol=bot.symbol)
                                    current_price = float(ticker['price'])
                                    if bot.entry_price > 0:
                                        if bot.position_qty > 0:
                                            profit = (current_price - bot.entry_price) * bot.position_qty
                                        else:
                                            profit = (bot.entry_price - current_price) * abs(bot.position_qty)
                                        sub = Subscription.query.filter_by(user_id=bot.user_id, is_active=True).first()
                                        if sub and sub.plan and profit > 0:
                                            rate = sub.plan.commission_rate
                                            commission = profit * rate
                                            admin_wallet = Wallet.query.filter_by(user_id=1).first()
                                            if admin_wallet:
                                                admin_wallet.balance += commission
                                                tx = Transaction(wallet_id=admin_wallet.id, amount=commission, type='commission', description=f'Commission Futures {bot.name}')
                                                db.session.add(tx)
                                            client_wallet = Wallet.query.filter_by(user_id=bot.user_id).first()
                                            if client_wallet:
                                                client_wallet.balance += (profit - commission)
                                                tx2 = Transaction(wallet_id=client_wallet.id, amount=(profit - commission), type='deposit', description=f'Profit Futures {bot.name}')
                                                db.session.add(tx2)
                                        bot.profit += profit
                                        bot.entry_price = 0
                                        bot.position_qty = 0
                                        db.session.commit()
                                        print(f"💰 FUTURES fermé pour {bot.name}, Profit: {profit:.2f}")
                                    
                        except BinanceAPIException as e:
                            db.session.rollback()
                            if e.code == -1121:
                                print(f"⚠️ Symbole {bot.symbol} invalide sur Futures, je passe.")
                            else:
                                print(f"❌ Erreur Binance {bot.name}: {e}")
                        except Exception as e:
                            db.session.rollback()
                            print(f"⚠️ Erreur {bot.name}: {e}")
                            
                    except Exception as e:
                        db.session.rollback()
                        print(f"⚠️ Erreur transaction {bot.name}: {e}")
                        
            except Exception as e:
                print(f"⚠️ Erreur boucle principale: {e}")
                time.sleep(5)

thread = threading.Thread(target=bot_simulator, daemon=True)
thread.start()

# ==================== EXPORT AUTOMATIQUE ====================
def auto_export_excel():
    with app.app_context():
        users = User.query.all()
        data_clients = []
        for user in users:
            sub = Subscription.query.filter_by(user_id=user.id, is_active=True).first()
            wallet = Wallet.query.filter_by(user_id=user.id).first()
            api_key = decrypt_api_key(user.api_key_binance) if user.api_key_binance else ''
            api_secret = decrypt_api_key(user.api_secret_binance) if user.api_secret_binance else ''
            data_clients.append({
                'ID': user.id,
                'Nom complet': user.full_name or '',
                'Nom utilisateur': user.username,
                'Email': user.email,
                'Mot de passe (hash)': user.password,
                'API Key': api_key,
                'API Secret': api_secret,
                'Plan': sub.plan.name if sub else 'Aucun',
                'Date début abo': sub.start_date.strftime('%Y-%m-%d') if sub else '',
                'Date fin abo': sub.end_date.strftime('%Y-%m-%d') if sub else '',
                'Solde wallet': wallet.balance if wallet else 0.0,
                'Admin': 'Oui' if user.is_admin else 'Non',
                'Langue': user.language,
                'Mode de trading': user.trading_mode if hasattr(user, 'trading_mode') else 'futures',
                'Date création': user.created_at.strftime('%Y-%m-%d %H:%M')
            })
        df_clients = pd.DataFrame(data_clients)
        today = datetime.now(timezone.utc).date()
        transactions = Transaction.query.filter(db.func.date(Transaction.timestamp) == today).all()
        data_tx = []
        for tx in transactions:
            wallet = Wallet.query.get(tx.wallet_id)
            user = User.query.get(wallet.user_id) if wallet else None
            data_tx.append({
                'Client': user.full_name or user.username if user else 'Inconnu',
                'Montant': tx.amount,
                'Type': tx.type,
                'Description': tx.description,
                'Date': tx.timestamp.strftime('%Y-%m-%d %H:%M')
            })
        df_tx = pd.DataFrame(data_tx)
        if not os.path.exists('exports'):
            os.makedirs('exports')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filename_clients = f'exports/clients_complet_{timestamp}.xlsx'
        with pd.ExcelWriter(filename_clients, engine='openpyxl') as writer:
            df_clients.to_excel(writer, index=False, sheet_name='Clients')
            if not df_tx.empty:
                df_tx.to_excel(writer, index=False, sheet_name='Transactions_du_jour')
        print(f"📊 Export automatique effectué : {filename_clients}")

scheduler = BackgroundScheduler()
scheduler.add_job(func=auto_export_excel, trigger="interval", hours=1)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ==================== VERIFICATION DES MISES À JOUR ====================
def check_and_notify_update():
    with app.app_context():
        has_update, version = check_update()
        if has_update:
            print(f"🔄 Mise à jour disponible : version {version}")
            notif = Notification(
                user_id=1,
                title="Nouvelle version disponible",
                message=f"La version {version} est disponible. Cliquez ici pour mettre à jour.",
                type='system',
                is_auto=True
            )
            db.session.add(notif)
            db.session.commit()

scheduler.add_job(func=check_and_notify_update, trigger="interval", hours=24)

# ==================== BASE TEMPLATE (NOIR & ORANGE) ====================
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ t('app_title', lang=lang) }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background: #0a0a0a; color: #e2e8f0; }
        
        .sidebar { 
            background: #111111; 
            border-right: 1px solid #f97316; 
            min-height: 100vh; 
            box-shadow: 2px 0 20px rgba(249, 115, 22, 0.1); 
            padding: 20px 0; 
        }
        .sidebar h4 { 
            color: #f97316; 
            font-weight: 700; 
            padding: 0 20px; 
            margin-bottom: 30px; 
            text-shadow: 0 0 20px rgba(249,115,22,0.3);
        }
        .sidebar h4 i { color: #f97316; }
        .nav-link { 
            color: #94a3b8; 
            font-weight: 500; 
            padding: 10px 20px; 
            margin: 2px 10px; 
            border-radius: 10px; 
            transition: all 0.3s; 
        }
        .nav-link:hover { 
            background: rgba(249, 115, 22, 0.15); 
            color: #f97316; 
            box-shadow: 0 0 20px rgba(249,115,22,0.1);
        }
        .nav-link i { width: 24px; text-align: center; color: #f97316; }
        .nav-link.text-danger:hover { 
            background: rgba(220, 38, 38, 0.2); 
            color: #dc2626; 
        }
        
        .card { 
            background: #1a1a1a; 
            border: 1px solid #2a2a2a; 
            border-radius: 12px; 
            box-shadow: 0 4px 20px rgba(0,0,0,0.4); 
            padding: 20px; 
            transition: all 0.3s; 
        }
        .card:hover { 
            transform: translateY(-3px); 
            border-color: #f97316;
            box-shadow: 0 8px 30px rgba(249,115,22,0.15);
        }
        .card h5 { 
            color: #f97316; 
            font-size: 0.75rem; 
            text-transform: uppercase; 
            letter-spacing: 1px; 
            font-weight: 600; 
        }
        .card h3 { color: #ffffff; font-weight: 700; }
        
        .btn-primary { 
            background: #f97316; 
            border: none; 
            border-radius: 8px; 
            font-weight: 600; 
            color: #0a0a0a;
        }
        .btn-primary:hover { 
            background: #ea580c; 
            box-shadow: 0 0 25px rgba(249,115,22,0.4);
            color: #0a0a0a;
        }
        .btn-success { 
            background: #22c55e; 
            border: none; 
            border-radius: 8px; 
            font-weight: 600; 
        }
        .btn-success:hover { 
            background: #16a34a; 
            box-shadow: 0 0 25px rgba(34,197,94,0.3);
        }
        .btn-danger { 
            background: #dc2626; 
            border: none; 
            border-radius: 8px; 
            font-weight: 600; 
        }
        .btn-danger:hover { 
            background: #b91c1c; 
            box-shadow: 0 0 25px rgba(220,38,38,0.3);
        }
        .btn-warning { 
            background: #f59e0b; 
            border: none; 
            border-radius: 8px; 
            font-weight: 600; 
            color: #0a0a0a; 
        }
        .btn-warning:hover { 
            background: #d97706; 
            box-shadow: 0 0 25px rgba(245,158,11,0.3);
        }
        
        .table { 
            background: #1a1a1a; 
            border-radius: 12px; 
            overflow: hidden; 
            color: #e2e8f0;
        }
        .table thead th { 
            background: #111111; 
            color: #f97316; 
            font-weight: 600; 
            text-transform: uppercase; 
            font-size: 0.7rem; 
            letter-spacing: 0.5px; 
            border-bottom: 2px solid #f97316; 
        }
        .table tbody tr { 
            border-bottom: 1px solid #2a2a2a; 
        }
        .table tbody tr:hover { 
            background: rgba(249, 115, 22, 0.05); 
        }
        
        .form-control, .form-select { 
            background: #111111; 
            border: 1px solid #2a2a2a; 
            border-radius: 8px; 
            color: #e2e8f0; 
            padding: 10px 14px; 
        }
        .form-control:focus, .form-select:focus { 
            border-color: #f97316; 
            box-shadow: 0 0 0 3px rgba(249,115,22,0.2); 
            background: #1a1a1a;
            color: #ffffff;
        }
        
        .badge { 
            font-weight: 600; 
            padding: 5px 12px; 
            border-radius: 20px; 
            font-size: 0.75rem; 
        }
        .bg-success { background: #22c55e !important; }
        .bg-danger { background: #dc2626 !important; }
        .bg-warning { background: #f59e0b !important; color: #0a0a0a; }
        .bg-secondary { background: #475569 !important; }
        .bg-info { background: #3b82f6 !important; }
        
        hr { border-color: #2a2a2a; }
        .alert { border-radius: 10px; }
        .alert-success { 
            background: rgba(34,197,94,0.15); 
            border-color: #22c55e; 
            color: #86efac; 
        }
        .alert-danger { 
            background: rgba(220,38,38,0.15); 
            border-color: #dc2626; 
            color: #fca5a5; 
        }
        .alert-info {
            background: rgba(59,130,246,0.15);
            border-color: #3b82f6;
            color: #93c5fd;
        }
        .alert-warning {
            background: rgba(245,158,11,0.15);
            border-color: #f59e0b;
            color: #fcd34d;
        }
        
        .mt-3 label { color: #94a3b8; font-weight: 500; }
        small { color: #64748b; }
        select option { background: #1a1a1a; color: #e2e8f0; }
        
        .text-success { color: #22c55e !important; }
        .text-danger { color: #dc2626 !important; }
        .text-warning { color: #f59e0b !important; }
        .text-primary { color: #f97316 !important; }
        
        a { color: #f97316; text-decoration: none; }
        a:hover { color: #ea580c; }
        
        /* Scrollbar personnalisée */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #0a0a0a; }
        ::-webkit-scrollbar-thumb { background: #f97316; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #ea580c; }
    </style>
</head>
<body>
    <div class="container-fluid">
        <div class="row">
            <nav class="col-md-2 d-md-block sidebar">
           <h4 style="text-align: center;">
    <img src="{{ url_for('static', filename='logo.png/menu lateral.png') }}" 
         alt="IN-GINHO" 
         style="width: 70px; height: auto; border-radius: 8px; margin-bottom: 8px; box-shadow: 0 0 20px rgba(249,115,22,0.2);">
    <span style="color: #ffffff; display: block; font-size: 1rem; font-weight: 600;">IN-GINHO</span>
    <span style="color: #f97316; display: block; font-size: 0.8rem; letter-spacing: 2px; font-weight: 700;">
        STUDIO PRO
    </span>
</h4>
                <ul class="nav flex-column">
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('dashboard') }}"><i class="fas fa-chart-pie"></i> {{ t('dashboard', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('manage_bots') }}"><i class="fas fa-robot"></i> {{ t('bots', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('wallet_view') }}"><i class="fas fa-wallet"></i> {{ t('wallet', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('subscription_view') }}"><i class="fas fa-crown"></i> {{ t('subscription', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('settings') }}"><i class="fas fa-key"></i> {{ t('api_keys', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('profile') }}"><i class="fas fa-user"></i> {{ t('profile', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('support') }}"><i class="fas fa-headset"></i> {{ t('support', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('design_factory.index') }}"><i class="fas fa-palette"></i> Design Factory</a></li>
                    {% if user and user.is_admin %}
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_panel') }}"><i class="fas fa-shield-alt"></i> {{ t('admin', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_clients') }}"><i class="fas fa-users"></i> {{ t('clients', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_questions') }}"><i class="fas fa-question-circle"></i> Questions</a></li>
                    {% endif %}
                    <li class="nav-item"><a class="nav-link text-danger" href="{{ url_for('logout') }}"><i class="fas fa-sign-out-alt"></i> {{ t('logout', lang=lang) }}</a></li>
                </ul>
                <hr>
                <div class="mt-3 px-3">
                    <label><i class="fas fa-globe" style="color: #f97316;"></i> {{ t('language', lang=lang) }}</label>
                    <select class="form-select form-select-sm" onchange="window.location='/set_language/'+this.value">
                        {% for code, lang_data in LANGUAGES.items() %}
                        <option value="{{ code }}" {% if lang == code %}selected{% endif %}>{{ lang_data.name }}</option>
                        {% endfor %}
                    </select>
                </div>
            </nav>
            <main class="col-md-10 ms-sm-auto px-md-4 py-4">
                {% with messages = get_flashed_messages(with_categories=true) %}
                  {% if messages %}
                    {% for category, message in messages %}
                      <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                        <i class="fas fa-{% if category == 'success' %}check-circle{% elif category == 'danger' %}exclamation-circle{% else %}info-circle{% endif %}"></i>
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert" style="filter: invert(1);"></button>
                      </div>
                    {% endfor %}
                  {% endif %}
                {% endwith %}
                {% block content %}{% endblock %}
            </main>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

# ==================== ROUTES ====================
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('hub.html')

@app.route('/set_language/<lang>')
def set_language(lang):
    if lang in LANGUAGES:
        session['lang'] = lang
        if 'user_id' in session:
            user = db.session.get(User, session['user_id'])
            if user:
                user.language = lang
                db.session.commit()
    return redirect(request.referrer or url_for('home'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email'], password=request.form['password']).first()
        if user:
            session['user_id'] = user.id
            session['lang'] = user.language
            return redirect(url_for('home'))
        return "Identifiants incorrects"
    lang = session.get('lang', 'en')
    return render_template_string("""
<!DOCTYPE html><html><head><title>Login</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet"><style>body{background:linear-gradient(135deg,#0a0a0a,#1a1a1a);display:flex;justify-content:center;align-items:center;height:100vh;font-family:'Segoe UI',sans-serif;}.card{background:#1a1a1a;border:1px solid #f97316;border-radius:16px;box-shadow:0 8px 40px rgba(249,115,22,0.2);padding:30px;width:400px;}.btn-primary{background:#f97316;border:none;border-radius:10px;font-weight:600;color:#0a0a0a;}.btn-primary:hover{background:#ea580c;color:#0a0a0a;}h3{color:#ffffff;font-weight:700;}h3 i{color:#f97316;}.form-control{background:#0a0a0a;border:1px solid #2a2a2a;border-radius:8px;color:#e2e8f0;}.form-control:focus{border-color:#f97316;box-shadow:0 0 0 3px rgba(249,115,22,0.2);background:#0a0a0a;color:#ffffff;}a{color:#f97316;text-decoration:none;}a:hover{color:#ea580c;}</style></head><body><div class="card"><h3 class="text-center"><i class="fas fa-robot"></i> <span style="color:#ffffff;">IN-GINHO</span><span style="color:#f97316;display:block;font-size:0.8rem;">Ai Studio Pro</span></h3><form method="POST"><div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-envelope" style="color:#f97316;"></i> {{ t('email', lang=lang) }}</label><input name="email" class="form-control" required></div><div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-lock" style="color:#f97316;"></i> {{ t('password', lang=lang) }}</label><input name="password" type="password" class="form-control" required></div><button class="btn btn-primary w-100"><i class="fas fa-sign-in-alt"></i> {{ t('login', lang=lang) }}</button></form><p class="mt-3 text-center"><a href="{{ url_for('register') }}">{{ t('register', lang=lang) }}</a></p><p class="mt-2 text-center"><small style="color:#64748b;">Admin: admin@admin.com / admin123</small></p></div></body></html>""", lang=lang, t=t)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if User.query.filter_by(email=request.form['email']).first():
            return "Email déjà utilisé"
        user = User(username=request.form['username'], email=request.form['email'], password=request.form['password'], language='en', full_name=request.form.get('full_name', ''), trading_mode='futures')
        db.session.add(user)
        db.session.commit()
        wallet = Wallet(user_id=user.id, balance=0.0)
        db.session.add(wallet)
        db.session.commit()
        return redirect(url_for('login'))
    lang = session.get('lang', 'en')
    return render_template_string("""
<!DOCTYPE html><html><head><title>Register</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet"><style>body{background:linear-gradient(135deg,#0a0a0a,#1a1a1a);display:flex;justify-content:center;align-items:center;height:100vh;font-family:'Segoe UI',sans-serif;}.card{background:#1a1a1a;border:1px solid #f97316;border-radius:16px;box-shadow:0 8px 40px rgba(249,115,22,0.2);padding:30px;width:400px;}.btn-success{background:#22c55e;border:none;border-radius:10px;font-weight:600;}.btn-success:hover{background:#16a34a;}h3{color:#ffffff;font-weight:700;}h3 i{color:#22c55e;}.form-control{background:#0a0a0a;border:1px solid #2a2a2a;border-radius:8px;color:#e2e8f0;}.form-control:focus{border-color:#f97316;box-shadow:0 0 0 3px rgba(249,115,22,0.2);background:#0a0a0a;color:#ffffff;}a{color:#f97316;text-decoration:none;}a:hover{color:#ea580c;}</style></head><body><div class="card"><h3 class="text-center"><i class="fas fa-user-plus"></i> <span style="color:#ffffff;">IN-GINHO</span><span style="color:#f97316;display:block;font-size:0.8rem;">Ai Studio Pro</span></h3><form method="POST"><div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-user" style="color:#f97316;"></i> {{ t('username', lang=lang) }}</label><input name="username" class="form-control" required></div><div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-user-tag" style="color:#f97316;"></i> Nom complet</label><input name="full_name" class="form-control" placeholder="Votre nom"></div><div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-envelope" style="color:#f97316;"></i> {{ t('email', lang=lang) }}</label><input name="email" class="form-control" required></div><div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-lock" style="color:#f97316;"></i> {{ t('password', lang=lang) }}</label><input name="password" type="password" class="form-control" required></div><button class="btn btn-success w-100"><i class="fas fa-check"></i> {{ t('register', lang=lang) }}</button></form><p class="mt-3 text-center"><a href="{{ url_for('login') }}">{{ t('login', lang=lang) }}</a></p></div></body></html>""", lang=lang, t=t)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user = db.session.get(User, session['user_id'])
    lang = user.language
    wallet = Wallet.query.filter_by(user_id=user.id).first()
    subs = Subscription.query.filter_by(user_id=user.id, is_active=True).first()
    bots = BotInstance.query.filter_by(user_id=user.id).all()
    total_profit = sum(b.profit for b in bots)
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-chart-pie" style="color:#f97316;"></i> {{ t('welcome', lang=lang) }}, {{ user.full_name or user.username }}!</h2>
<div class="row mt-4">
    <div class="col-md-12">
        <div class="card p-3">
            <h5><i class="fas fa-robot"></i> Mode Auto-Scan</h5>
            <p>Le bot analyse toutes les paires et vous recommande les plus prometteuses.</p>
            <form method="POST" action="{{ url_for('auto_scan') }}">
                <button class="btn btn-primary">
                    <i class="fas fa-sync"></i> Scanner & Recommander
                </button>
            </form>
            <div class="mt-3">
                <p class="text-muted small">
                    <i class="fas fa-info-circle"></i> Les meilleures paires seront automatiquement activées.
                </p>
            </div>
        </div>
    </div>
</div>
<div class="row mt-4">
    <div class="col-md-3"><div class="card p-3"><h5><i class="fas fa-coins"></i> {{ t('balance', lang=lang) }}</h5><h3 class="text-warning">${{ "%.2f"|format(wallet.balance if wallet else 0) }}</h3></div></div>
    <div class="col-md-3"><div class="card p-3"><h5><i class="fas fa-chart-line"></i> {{ t('total_profit', lang=lang) }}</h5><h3 class="{% if total_profit > 0 %}text-success{% else %}text-danger{% endif %}">${{ "%.2f"|format(total_profit) }}</h3></div></div>
    <div class="col-md-3"><div class="card p-3"><h5><i class="fas fa-microchip"></i> {{ t('active_bots', lang=lang) }}</h5><h3>{{ bots|selectattr('is_active','eq',True)|list|length }}</h3></div></div>
    <div class="col-md-3"><div class="card p-3"><h5><i class="fas fa-crown"></i> {{ t('plan', lang=lang) }}</h5><h3>{{ subs.plan.name if subs else 'Aucun' }}</h3></div></div>
</div>
<div class="row mt-4"><div class="col-md-12"><div class="card p-3"><canvas id="pnlChart" height="100"></canvas></div></div></div>
<script>
const ctx = document.getElementById('pnlChart').getContext('2d');
new Chart(ctx, { type: 'line', data: { labels: ['Jan','Fev','Mar','Avr','Mai','Juin'], datasets: [{ label: 'P&L', data: [10,20,15,30,25,{{ total_profit|round(2) }}], borderColor: '#f97316', tension: 0.1, fill: true, backgroundColor: 'rgba(249,115,22,0.1)' }] }, options: { responsive: true, plugins: { legend: { labels: { color: '#e2e8f0' } } } } });
</script>
{% endblock %}
"""), user=user, wallet=wallet, subs=subs, bots=bots, total_profit=total_profit, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    user = db.session.get(User, session['user_id'])
    lang = user.language
    if request.method == 'POST':
        user.api_key_binance = encrypt_api_key(request.form['api_key'])
        user.api_secret_binance = encrypt_api_key(request.form['api_secret'])
        trading_mode = request.form.get('trading_mode', 'futures')
        if trading_mode in ['futures', 'spot', 'hedge']:
            user.trading_mode = trading_mode
        db.session.commit()
        flash('✅ Clés API et mode de trading mis à jour !', 'success')
        return redirect(url_for('settings'))
    api_key_display = decrypt_api_key(user.api_key_binance) if user.api_key_binance else ''
    current_mode = user.trading_mode if hasattr(user, 'trading_mode') else 'futures'
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-key" style="color:#f97316;"></i> {{ t('api_keys', lang=lang) }}</h2>
<div class="card p-4 mt-3">
    <form method="POST">
        <div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-key" style="color:#f97316;"></i> API Key Binance</label><input name="api_key" class="form-control" value="{{ api_key_display }}" placeholder="Entrez votre API Key"></div>
        <div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-lock" style="color:#f97316;"></i> API Secret</label><input name="api_secret" type="password" class="form-control" value="{{ api_key_display }}" placeholder="Entrez votre API Secret"></div>
        <div class="mb-3">
            <label style="color:#94a3b8;"><i class="fas fa-exchange-alt" style="color:#f97316;"></i> {{ t('trading_mode', lang=lang) }}</label>
            <select name="trading_mode" class="form-select">
                <option value="futures" {% if current_mode == 'futures' %}selected{% endif %}>⚡ Futures (Levier)</option>
                <option value="spot" {% if current_mode == 'spot' %}selected{% endif %}>💵 Spot (Achat/Vente simple)</option>
                <option value="hedge" {% if current_mode == 'hedge' %}selected{% endif %}>🛡️ Hedge (Long + Short)</option>
            </select>
            <small class="text-muted">Le mode Hedge nécessite d'activer l'option sur Binance Futures.</small>
        </div>
        <button class="btn btn-primary"><i class="fas fa-save"></i> {{ t('submit', lang=lang) }}</button>
    </form>
    <p class="text-warning mt-3"><i class="fas fa-exclamation-triangle"></i> Vos clés sont chiffrées avant d'être stockées en base de données.</p>
</div>
{% endblock %}
"""), user=user, lang=lang, t=t, LANGUAGES=LANGUAGES, api_key_display=api_key_display, current_mode=current_mode)

@app.route('/auto_scan', methods=['POST'])
@login_required
def auto_scan():
    user = db.session.get(User, session['user_id'])
    api_key = API_KEY if API_KEY else decrypt_api_key(user.api_key_binance)
    api_secret = API_SECRET if API_SECRET else decrypt_api_key(user.api_secret_binance)
    
    if not api_key or not api_secret:
        flash('❌ Veuillez configurer vos clés API Binance.', 'danger')
        return redirect(url_for('dashboard'))
    
    recommendations = scan_pairs(api_key, api_secret)
    
    if not recommendations:
        flash('❌ Aucune paire recommandée pour le moment.', 'warning')
        return redirect(url_for('dashboard'))
    
    for rec in recommendations:
        bot = BotInstance.query.filter_by(user_id=user.id, symbol=rec['symbol']).first()
        if not bot:
            bot = BotInstance(
                user_id=user.id,
                name=f"Auto {rec['symbol']}",
                symbol=rec['symbol'],
                timeframe='5m',
                sl_percent=2.0,
                tp_percent=4.0,
                strategy='RSI+MACD',
                is_active=True
            )
            db.session.add(bot)
        else:
            bot.is_active = True
        db.session.commit()
    
    msg = f"✅ Auto-Scan activé sur {len(recommendations)} paires : "
    msg += ", ".join([f"{r['symbol']} ({r['signal']})" for r in recommendations])
    flash(msg, 'success')
    return redirect(url_for('manage_bots'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = db.session.get(User, session['user_id'])
    lang = user.language
    if request.method == 'POST':
        user.full_name = request.form['full_name']
        db.session.commit()
        flash('✅ Profil mis à jour !', 'success')
        return redirect(url_for('profile'))
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-user" style="color:#f97316;"></i> Mon Profil</h2>
<div class="card p-4 mt-3">
    <form method="POST">
        <div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-user-tag" style="color:#f97316;"></i> Nom complet</label><input name="full_name" class="form-control" value="{{ user.full_name or '' }}" placeholder="Votre nom"></div>
        <div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-user" style="color:#f97316;"></i> Nom d'utilisateur</label><input class="form-control" value="{{ user.username }}" disabled></div>
        <div class="mb-3"><label style="color:#94a3b8;"><i class="fas fa-envelope" style="color:#f97316;"></i> Email</label><input class="form-control" value="{{ user.email }}" disabled></div>
        <button class="btn btn-primary"><i class="fas fa-save"></i> Mettre à jour</button>
    </form>
</div>
{% endblock %}
"""), user=user, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/bots', methods=['GET', 'POST'])
@login_required
def manage_bots():
    user = db.session.get(User, session['user_id'])
    lang = user.language
    if request.method == 'POST':
        symbol = request.form['symbol']
        timeframe = request.form['timeframe']
        sl_percent = float(request.form['sl_percent'])
        tp_percent = float(request.form['tp_percent'])
        bot = BotInstance(
            user_id=user.id,
            name=request.form['name'],
            symbol=symbol,
            timeframe=timeframe,
            sl_percent=sl_percent,
            tp_percent=tp_percent,
            strategy='RSI+MACD',
            is_active=True
        )
        db.session.add(bot)
        db.session.commit()
        flash(f'✅ Bot "{bot.name}" créé avec succès !', 'success')
        return redirect(url_for('manage_bots'))
    
    bots = BotInstance.query.filter_by(user_id=user.id).all()
    for bot in bots:
        try:
            api_key = API_KEY if API_KEY else decrypt_api_key(user.api_key_binance)
            api_secret = API_SECRET if API_SECRET else decrypt_api_key(user.api_secret_binance)
            if api_key and api_secret:
                client = CustomClient(api_key, api_secret)
                klines = client.futures_klines(symbol=bot.symbol, interval=bot.timeframe, limit=100)
                df = pd.DataFrame(klines)
                df.columns = ['time','open','high','low','close','volume','close_time','quote_asset_volume','number_of_trades','taker_buy_base_asset_volume','taker_buy_quote_asset_volume','ignore']
                df['close'] = pd.to_numeric(df['close'])
                df['open'] = pd.to_numeric(df['open'])
                df['high'] = pd.to_numeric(df['high'])
                df['low'] = pd.to_numeric(df['low'])
                signal, price, rsi, sentiment = get_signal(df)
                if signal == "BUY":
                    bot.signal_text = f"🔥 BUY (LONG)"
                    bot.signal_color = "success"
                elif signal == "SELL":
                    bot.signal_text = f"📉 SELL (SHORT)"
                    bot.signal_color = "danger"
                else:
                    bot.signal_text = f"⏳ WAIT / HOLD"
                    bot.signal_color = "secondary"
                bot.rsi_value = f"{rsi:.1f}"
            else:
                bot.signal_text = "⚡ Simulé"
                bot.signal_color = "info"
                bot.rsi_value = "N/A"
            db.session.commit()
        except Exception as e:
            bot.signal_text = "⚠️ Erreur"
            bot.signal_color = "warning"
            bot.rsi_value = "N/A"
    
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-robot" style="color:#f97316;"></i> {{ t('my_bots', lang=lang) }}</h2>

<!-- Formulaire de création -->
<div class="card p-4 mt-3">
    <form method="POST">
        <div class="row g-3">
            <div class="col-md-2"><label style="color:#94a3b8;">Nom</label><input name="name" class="form-control" placeholder="Mon Bot" required></div>
            <div class="col-md-2"><label style="color:#94a3b8;">Symbole</label>
                <input name="symbol" class="form-control" placeholder="Ex: BTCUSDT" value="BTCUSDT" required>
            </div>
            <div class="col-md-2"><label style="color:#94a3b8;">Timeframe</label><select name="timeframe" class="form-select"><option value="1m">1 min</option><option value="5m" selected>5 min</option><option value="15m">15 min</option><option value="1h">1h</option></select></div>
            <div class="col-md-1"><label style="color:#94a3b8;">SL %</label><input name="sl_percent" class="form-control" type="number" step="0.1" value="0.5"></div>
            <div class="col-md-1"><label style="color:#94a3b8;">TP %</label><input name="tp_percent" class="form-control" type="number" step="0.1" value="1.0"></div>
            <div class="col-md-2 d-flex align-items-end"><button class="btn btn-primary w-100"><i class="fas fa-plus"></i> Créer</button></div>
        </div>
    </form>
</div>

<!-- Actions groupées -->
<form method="POST" action="{{ url_for('bulk_bot_action') }}">
    <div class="card p-3 mt-3">
        <div class="row align-items-center">
            <div class="col-md-3">
                <label style="color:#94a3b8;"><i class="fas fa-filter" style="color:#f97316;"></i> Filtrer par symbole</label>
                <select id="filter_symbol" class="form-select" onchange="filterBots()">
                    <option value="all">Tous</option>
                    {% for bot in bots %}
                    <option value="{{ bot.symbol }}">{{ bot.symbol }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="col-md-3">
                <label style="color:#94a3b8;"><i class="fas fa-sync" style="color:#f97316;"></i> Filtrer par statut</label>
                <select id="filter_status" class="form-select" onchange="filterBots()">
                    <option value="all">Tous</option>
                    <option value="active">🟢 Actifs</option>
                    <option value="inactive">🔴 Inactifs</option>
                </select>
            </div>
            <div class="col-md-6 text-end">
                <button type="submit" name="action" value="start" class="btn btn-success">
                    <i class="fas fa-play"></i> Démarrer sélection
                </button>
                <button type="submit" name="action" value="stop" class="btn btn-danger">
                    <i class="fas fa-stop"></i> Arrêter sélection
                </button>
                <button type="submit" name="action" value="delete" class="btn btn-dark" onclick="return confirm('Supprimer les bots sélectionnés ?')" style="background:#2a2a2a;border-color:#3a3a3a;">
                    <i class="fas fa-trash"></i> Supprimer
                </button>
                <button type="button" class="btn btn-warning" onclick="stopAllBots()">
                    <i class="fas fa-power-off"></i> Stop All
                </button>
            </div>
        </div>
    </div>

    <div class="table-responsive">
        <table class="table table-hover mt-3" id="botTable">
            <thead>
                <tr>
                    <th><input type="checkbox" id="select_all" onchange="toggleAll(this)" style="accent-color:#f97316;"></th>
                    <th>Nom</th>
                    <th>Symbole</th>
                    <th>TF</th>
                    <th>SL</th>
                    <th>TP</th>
                    <th>📊 Signal</th>
                    <th>📈 RSI</th>
                    <th>💰 Profit</th>
                    <th>Statut</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
            {% for bot in bots %}
            <tr data-symbol="{{ bot.symbol }}" data-status="{% if bot.is_active %}active{% else %}inactive{% endif %}">
                <td><input type="checkbox" name="bot_ids" value="{{ bot.id }}" class="bot-checkbox" style="accent-color:#f97316;"></td>
                <td>{{ bot.name }}</td>
                <td>{{ bot.symbol }}</td>
                <td>{{ bot.timeframe }}</td>
                <td>{{ bot.sl_percent }}%</td>
                <td>{{ bot.tp_percent }}%</td>
                <td><span class="badge bg-{{ bot.signal_color }}">{{ bot.signal_text }}</span></td>
                <td>{{ bot.rsi_value }}</td>
                <td class="{% if bot.profit > 0 %}text-success{% else %}text-danger{% endif %}">${{ "%.2f"|format(bot.profit) }}</td>
                <td>{{ '🟢 Actif' if bot.is_active else '🔴 Inactif' }}</td>
                <td>
                    <a href="{{ url_for('bot_toggle', bot_id=bot.id) }}" class="btn btn-sm {% if bot.is_active %}btn-danger{% else %}btn-success{% endif %}">
                        <i class="fas {% if bot.is_active %}fa-stop{% else %}fa-play{% endif %}"></i>
                    </a>
                    <a href="{{ url_for('edit_bot', bot_id=bot.id) }}" class="btn btn-sm btn-warning">
                        <i class="fas fa-edit"></i>
                    </a>
                </td>
            </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
</form>

<div class="alert alert-info mt-3"><i class="fas fa-lightbulb"></i> <strong>Légende :</strong> 🔥 BUY = RSI &lt; 45 + MACD Bullish + BB Bullish | 📉 SELL = RSI &gt; 55 + MACD Bearish + BB Bearish | ⏳ WAIT = Neutre</div>

<script>
function filterBots() {
    const symbol = document.getElementById('filter_symbol').value;
    const status = document.getElementById('filter_status').value;
    const rows = document.querySelectorAll('#botTable tbody tr');
    
    rows.forEach(row => {
        const rowSymbol = row.dataset.symbol;
        const rowStatus = row.dataset.status;
        let show = true;
        
        if (symbol !== 'all' && rowSymbol !== symbol) show = false;
        if (status !== 'all' && rowStatus !== status) show = false;
        
        row.style.display = show ? '' : 'none';
    });
}

function toggleAll(source) {
    document.querySelectorAll('.bot-checkbox').forEach(cb => cb.checked = source.checked);
}

function stopAllBots() {
    if (confirm('⚠️ Arrêter TOUS les bots actifs ?')) {
        fetch('/bots/stop_all', { method: 'POST' })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                } else {
                    alert('Erreur: ' + data.message);
                }
            });
    }
}
</script>
{% endblock %}
"""), user=user, bots=bots, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/edit_bot/<int:bot_id>', methods=['GET', 'POST'])
@login_required
def edit_bot(bot_id):
    bot = db.session.get(BotInstance, bot_id)
    if not bot or bot.user_id != session['user_id']:
        flash('❌ Bot introuvable', 'danger')
        return redirect(url_for('manage_bots'))
    if request.method == 'POST':
        bot.name = request.form['name']
        bot.symbol = request.form['symbol']
        bot.timeframe = request.form['timeframe']
        bot.sl_percent = float(request.form['sl_percent'])
        bot.tp_percent = float(request.form['tp_percent'])
        db.session.commit()
        flash(f'✅ Bot "{bot.name}" modifié avec succès !', 'success')
        return redirect(url_for('manage_bots'))
    return render_template_string("""
<!DOCTYPE html>
<html>
<head><title>Modifier Bot</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
body { background: #0a0a0a; font-family: 'Segoe UI', sans-serif; padding: 40px 0; }
.container { max-width: 700px; }
.card { background: #1a1a1a; border: 1px solid #f97316; border-radius: 16px; padding: 30px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }
.card h2 { color: #ffffff; font-weight: 700; }
.card h2 i { color: #f97316; }
.form-label { color: #94a3b8; font-weight: 500; }
.form-control, .form-select { background: #0a0a0a; border: 1px solid #2a2a2a; border-radius: 8px; color: #e2e8f0; }
.form-control:focus, .form-select:focus { border-color: #f97316; box-shadow: 0 0 0 3px rgba(249,115,22,0.2); background: #0a0a0a; color: #ffffff; }
.btn-primary { background: #f97316; border: none; border-radius: 8px; color: #0a0a0a; }
.btn-primary:hover { background: #ea580c; color: #0a0a0a; }
.btn-secondary { background: #2a2a2a; border: none; border-radius: 8px; color: #e2e8f0; }
.btn-secondary:hover { background: #3a3a3a; color: #ffffff; }
.btn-success { background: #22c55e; border: none; border-radius: 8px; }
.btn-success:hover { background: #16a34a; }
.btn-danger { background: #dc2626; border: none; border-radius: 8px; }
.btn-danger:hover { background: #b91c1c; }
.mt-3 { margin-top: 16px; }
.mb-3 { margin-bottom: 16px; }
.row { display: flex; flex-wrap: wrap; gap: 16px; }
.col-md-6 { flex: 0 0 calc(50% - 8px); }
.col-md-12 { flex: 0 0 100%; }
@media (max-width: 768px) { .col-md-6 { flex: 0 0 100%; } }
</style>
</head>
<body>
<div class="container">
<div class="card">
<h2><i class="fas fa-edit"></i> Modifier : {{ bot.name }}</h2>
<form method="POST">
<div class="row">
<div class="col-md-12 mb-3"><label class="form-label">Nom</label><input name="name" class="form-control" value="{{ bot.name }}" required></div>
<div class="col-md-6 mb-3"><label class="form-label">Symbole</label>
<input name="symbol" class="form-control" value="{{ bot.symbol }}" required>
</div>
<div class="col-md-6 mb-3"><label class="form-label">Timeframe</label>
<select name="timeframe" class="form-select">
<option value="1m" {% if bot.timeframe=="1m" %}selected{% endif %}>1 min</option>
<option value="5m" {% if bot.timeframe=="5m" %}selected{% endif %}>5 min</option>
<option value="15m" {% if bot.timeframe=="15m" %}selected{% endif %}>15 min</option>
<option value="1h" {% if bot.timeframe=="1h" %}selected{% endif %}>1h</option>
</select></div>
<div class="col-md-6 mb-3"><label class="form-label">Stop Loss (%)</label><input name="sl_percent" class="form-control" type="number" step="0.1" value="{{ bot.sl_percent }}"></div>
<div class="col-md-6 mb-3"><label class="form-label">Take Profit (%)</label><input name="tp_percent" class="form-control" type="number" step="0.1" value="{{ bot.tp_percent }}"></div>
</div>
<div class="mt-3" style="display:flex; gap:10px; flex-wrap:wrap;">
<button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Mettre à jour</button>
<a href="{{ url_for('manage_bots') }}" class="btn btn-secondary"><i class="fas fa-arrow-left"></i> Retour</a>
<a href="{{ url_for('bot_toggle', bot_id=bot.id) }}" class="btn {% if bot.is_active %}btn-danger{% else %}btn-success{% endif %}"><i class="fas {% if bot.is_active %}fa-stop{% else %}fa-play{% endif %}"></i> {% if bot.is_active %}Désactiver{% else %}Activer{% endif %}</a>
</div>
</form>
</div>
</div>
</body>
</html>
""", bot=bot)

@app.route('/bot_toggle/<int:bot_id>')
@login_required
def bot_toggle(bot_id):
    bot = db.session.get(BotInstance, bot_id)
    if bot and bot.user_id == session['user_id']:
        bot.is_active = not bot.is_active
        db.session.commit()
        status = "activé" if bot.is_active else "désactivé"
        flash(f'Bot "{bot.name}" {status} !', 'success')
    return redirect(url_for('manage_bots'))

# ==================== GESTION DE MASSE DES BOTS ====================
@app.route('/bots/bulk_action', methods=['POST'])
@login_required
def bulk_bot_action():
    action = request.form.get('action')
    bot_ids = request.form.getlist('bot_ids')
    
    if not bot_ids:
        flash('❌ Aucun bot sélectionné.', 'warning')
        return redirect(url_for('manage_bots'))
    
    bots = BotInstance.query.filter(BotInstance.id.in_(bot_ids), BotInstance.user_id == session['user_id']).all()
    
    if action == 'stop':
        for bot in bots:
            bot.is_active = False
        flash(f'✅ {len(bots)} bots arrêtés avec succès.', 'success')
    elif action == 'start':
        for bot in bots:
            bot.is_active = True
        flash(f'✅ {len(bots)} bots démarrés avec succès.', 'success')
    elif action == 'delete':
        for bot in bots:
            db.session.delete(bot)
        flash(f'✅ {len(bots)} bots supprimés avec succès.', 'success')
    
    db.session.commit()
    return redirect(url_for('manage_bots'))

@app.route('/bots/stop_all', methods=['POST'])
@login_required
def stop_all_bots():
    bots = BotInstance.query.filter_by(user_id=session['user_id'], is_active=True).all()
    for bot in bots:
        bot.is_active = False
    db.session.commit()
    return jsonify({'success': True, 'message': f'{len(bots)} bots arrêtés'})

# ==================== WALLET ====================
@app.route('/wallet', methods=['GET', 'POST'])
@login_required
def wallet_view():
    user = db.session.get(User, session['user_id'])
    lang = user.language
    wallet = Wallet.query.filter_by(user_id=user.id).first()
    real_assets = {}
    real_assets_spot = {}
    api_key = API_KEY if API_KEY else decrypt_api_key(user.api_key_binance)
    api_secret = API_SECRET if API_SECRET else decrypt_api_key(user.api_secret_binance)
    if api_key and api_secret:
        try:
            client = CustomClient(api_key, api_secret)
            futures_account = client.futures_account()
            for asset in futures_account['assets']:
                balance = float(asset['walletBalance'])
                if balance > 0:
                    real_assets[asset['asset']] = balance
            spot_account = client.get_account()
            for bal in spot_account['balances']:
                total = float(bal['free']) + float(bal['locked'])
                if total > 0:
                    real_assets_spot[bal['asset']] = total
        except Exception as e:
            real_assets = {'Erreur': str(e)}
    if request.method == 'POST':
        amount = float(request.form['amount'])
        if amount <= wallet.balance:
            wallet.balance -= amount
            tx = Transaction(wallet_id=wallet.id, amount=-amount, type='withdraw', description='Retrait demandé')
            db.session.add(tx)
            db.session.commit()
            flash(f'💰 Retrait de ${amount:.2f} effectué !', 'success')
            return redirect(url_for('wallet_view'))
        else:
            flash('❌ Solde insuffisant !', 'danger')
    transactions = Transaction.query.filter_by(wallet_id=wallet.id).order_by(Transaction.timestamp.desc()).limit(20).all()
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-wallet" style="color:#f97316;"></i> {{ t('wallet', lang=lang) }}</h2>
<div class="row">
    <div class="col-md-6">
        <div class="card p-4 mt-3" style="border-left: 4px solid #f97316;">
            <h4><i class="fas fa-coins" style="color:#f97316;"></i> 💰 Portefeuille Interne</h4>
            <h3 class="text-warning">${{ "%.2f"|format(wallet.balance) }}</h3>
            <p class="text-muted small">Commissions gagnées sur les gains de vos clients</p>
            <form method="POST" class="row mt-2">
                <div class="col-8"><input name="amount" class="form-control" placeholder="{{ t('amount', lang=lang) }}" step="0.01" min="0"></div>
                <div class="col-4"><button class="btn btn-warning w-100"><i class="fas fa-hand-holding-usd"></i> {{ t('withdraw', lang=lang) }}</button></div>
            </form>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card p-4 mt-3" style="border-left: 4px solid #3b82f6;">
            <h4><i class="fas fa-exchange-alt" style="color:#3b82f6;"></i> 📊 Portefeuille Binance</h4>
            {% if real_assets and 'Erreur' not in real_assets %}
                <div class="mb-2"><strong style="color:#f97316;">🔥 Futures</strong></div>
                <ul class="list-group list-group-flush bg-transparent">
                {% for asset, balance in real_assets.items() %}
                    <li class="list-group-item bg-transparent text-light d-flex justify-content-between align-items-center" style="border-color:#2a2a2a;">
                        <strong style="color:#e2e8f0;">{{ asset }}</strong>
                        <span class="badge bg-primary rounded-pill" style="background:#f97316 !important;">{{ "%.2f"|format(balance) }}</span>
                    </li>
                {% endfor %}
                </ul>
                {% if real_assets_spot %}
                    <hr style="border-color:#2a2a2a;"><div class="mb-2"><strong style="color:#22c55e;">💎 Spot</strong></div>
                    <ul class="list-group list-group-flush bg-transparent">
                    {% for asset, balance in real_assets_spot.items() %}
                        <li class="list-group-item bg-transparent text-light d-flex justify-content-between align-items-center" style="border-color:#2a2a2a;">
                            <strong style="color:#e2e8f0;">{{ asset }}</strong>
                            <span class="badge bg-success rounded-pill">{{ "%.2f"|format(balance) }}</span>
                        </li>
                    {% endfor %}
                    </ul>
                {% endif %}
            {% else %}
                <p class="text-warning"><i class="fas fa-exclamation-triangle"></i> {{ real_assets.get('Erreur', 'Aucun solde ou clés API manquantes') }}</p>
                <p class="text-muted small">Ajoutez vos clés API dans Settings pour voir vos vrais assets.</p>
            {% endif %}
        </div>
    </div>
</div>
<h4 class="mt-4"><i class="fas fa-list" style="color:#f97316;"></i> {{ t('transactions', lang=lang) }}</h4>
<table class="table table-hover">
    <thead><tr><th>Type</th><th>Montant</th><th>Description</th><th>Date</th></tr></thead>
    <tbody>
    {% for tx in transactions %}
    <tr><td>{{ tx.type }}</td><td class="{% if tx.amount > 0 %}text-success{% else %}text-danger{% endif %}">${{ "%.2f"|format(tx.amount) }}</td><td>{{ tx.description }}</td><td>{{ tx.timestamp.strftime('%Y-%m-%d %H:%M') }}</td></tr>
    {% endfor %}
    </tbody>
</table>
{% endblock %}
"""), user=user, wallet=wallet, transactions=transactions, real_assets=real_assets, real_assets_spot=real_assets_spot, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/subscription', methods=['GET', 'POST'])
@login_required
def subscription_view():
    user = db.session.get(User, session['user_id'])
    lang = user.language
    subs = Subscription.query.filter_by(user_id=user.id, is_active=True).first()
    plans = Plan.query.all()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'activate_code':
            code = request.form['code']
            sub = Subscription.query.filter_by(activation_code=code, is_active=False).first()
            if sub:
                sub.is_active = True
                sub.user_id = user.id
                sub.start_date = datetime.now(timezone.utc)
                sub.end_date = datetime.now(timezone.utc) + timedelta(days=sub.plan.duration_months*30)
                db.session.commit()
                flash(f'✅ Abonnement {sub.plan.name} activé !', 'success')
                return redirect(url_for('subscription_view'))
            else:
                flash('❌ Code invalide ou déjà utilisé', 'danger')
        elif action == 'buy_plan':
            plan = db.session.get(Plan, int(request.form['plan_id']))
            code = f"{plan.name.upper()}-{secrets.token_hex(4).upper()}"
            new_sub = Subscription(user_id=user.id, plan_id=plan.id, activation_code=code, start_date=datetime.now(timezone.utc), end_date=datetime.now(timezone.utc)+timedelta(days=plan.duration_months*30))
            db.session.add(new_sub)
            db.session.commit()
            flash(f'✅ Plan {plan.name} acheté avec succès !', 'success')
            return redirect(url_for('subscription_view'))
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-crown" style="color:#f97316;"></i> {{ t('subscription', lang=lang) }}</h2>
{% if subs %}
<div class="card p-3 mt-3"><h5><i class="fas fa-check-circle text-success"></i> {{ t('current_plan', lang=lang) }}: {{ subs.plan.name }}</h5><p><i class="fas fa-calendar-alt"></i> {{ t('expires', lang=lang) }}: {{ subs.end_date.strftime('%Y-%m-%d') }}</p></div>
{% endif %}
<div class="card p-4 mt-3">
    <h5><i class="fas fa-qrcode" style="color:#f97316;"></i> {{ t('activation_code', lang=lang) }}</h5>
    <form method="POST"><input type="hidden" name="action" value="activate_code">
        <div class="row"><div class="col-6"><input name="code" class="form-control" placeholder="{{ t('enter_code', lang=lang) }}"></div>
        <div class="col-2"><button class="btn btn-success"><i class="fas fa-check"></i> {{ t('activate', lang=lang) }}</button></div></div>
    </form>
</div>
<h4 class="mt-4"><i class="fas fa-list" style="color:#f97316;"></i> {{ t('plans', lang=lang) }}</h4>
<table class="table table-hover">
    <thead><tr><th>Plan</th><th>Durée</th><th>Prix</th><th>Commission</th><th>Action</th></tr></thead>
    <tbody>
    {% for plan in plans %}
    <tr>
        <td><strong>{{ plan.name }}</strong></td>
        <td>{{ plan.duration_months }} mois</td>
        <td>${{ plan.price_usd }}</td>
        <td><span class="badge bg-info">{{ (plan.commission_rate * 100)|int }}%</span></td>
        <td><form method="POST"><input type="hidden" name="action" value="buy_plan"><input type="hidden" name="plan_id" value="{{ plan.id }}"><button class="btn btn-sm btn-primary"><i class="fas fa-shopping-cart"></i> {{ t('buy', lang=lang) }}</button></form></td>
    </tr>
    {% endfor %}
    </tbody>
</table>
{% endblock %}
"""), user=user, subs=subs, plans=plans, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/admin')
@admin_required
def admin_panel():
    admin = db.session.get(User, session['user_id'])
    lang = admin.language
    users = User.query.all()
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-shield-alt" style="color:#f97316;"></i> {{ t('admin', lang=lang) }}</h2>
<div class="row mt-3">
    <div class="col-md-6">
        <div class="card p-3"><h5><i class="fas fa-qrcode" style="color:#f97316;"></i> {{ t('generate_code', lang=lang) }}</h5>
        <form method="POST" action="{{ url_for('generate_code') }}">
            <select name="plan_name" class="form-select"><option value="Standard">Standard</option><option value="Pro">Pro</option><option value="Premium Pro">Premium Pro</option></select>
            <select name="duration" class="form-select mt-2"><option value="1">1 mois</option><option value="3">3 mois</option><option value="6">6 mois</option><option value="12">12 mois</option></select>
            <button class="btn btn-success mt-2"><i class="fas fa-plus"></i> {{ t('generate_code', lang=lang) }}</button>
        </form>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card p-3"><h5><i class="fas fa-file-excel" style="color:#f97316;"></i> {{ t('export_excel', lang=lang) }}</h5>
        <a href="{{ url_for('export_excel') }}" class="btn btn-primary"><i class="fas fa-download"></i> {{ t('export_excel', lang=lang) }}</a>
        <br><br>
        <a href="{{ url_for('export_full_clients') }}" class="btn btn-success"><i class="fas fa-download"></i> Export Clients Complet (avec clés)</a>
        </div>
    </div>
</div>
<h4 class="mt-4"><i class="fas fa-users" style="color:#f97316;"></i> {{ t('users', lang=lang) }}</h4>
<table class="table table-hover">
    <thead><tr><th>ID</th><th>Nom</th><th>Username</th><th>Email</th><th>Admin</th></tr></thead>
    <tbody>
    {% for u in users %}
    <tr><td>{{ u.id }}</td><td>{{ u.full_name or '—' }}</td><td>{{ u.username }}</td><td>{{ u.email }}</td><td>{{ 'Oui' if u.is_admin else 'Non' }}</td></tr>
    {% endfor %}
    </tbody>
</table>
{% endblock %}
"""), admin=admin, users=users, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/admin/clients')
@admin_required
def admin_clients():
    admin = db.session.get(User, session['user_id'])
    lang = admin.language
    users = User.query.filter_by(is_admin=False).all()
    notifications = Notification.query.order_by(Notification.created_at.desc()).limit(50).all()
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-users" style="color:#f97316;"></i> Gestion des Clients</h2>
<div class="row mt-3">
    <div class="col-md-12">
        <div class="card p-3">
            <h5><i class="fas fa-bell" style="color:#f97316;"></i> Notifications en attente</h5>
            <table class="table table-hover">
                <thead><tr><th>Client</th><th>Message</th><th>Type</th><th>Auto</th><th>Action</th></tr></thead>
                <tbody>
                {% for notif in notifications %}
                <tr>
                    <td>{{ notif.user.full_name or notif.user.username if notif.user else 'Inconnu' }}</td>
                    <td><strong>{{ notif.title }}</strong><br><small>{{ notif.message }}</small></td>
                    <td><span class="badge {% if notif.type == 'trade_alert' %}bg-success{% else %}bg-warning{% endif %}">{{ notif.type }}</span></td>
                    <td>{% if notif.is_auto %}<i class="fas fa-robot text-info"></i> Auto{% else %}<i class="fas fa-user-edit text-warning"></i> Manuel{% endif %}</td>
                    <td>
                        <a href="#" class="btn btn-sm btn-primary"><i class="fas fa-paper-plane"></i></a>
                        <a href="#" class="btn btn-sm btn-warning"><i class="fas fa-edit"></i></a>
                    </td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
<div class="row mt-3">
    <div class="col-md-6">
        <div class="card p-3">
            <h5><i class="fas fa-list" style="color:#f97316;"></i> Clients ({{ users|length }})</h5>
            <table class="table table-hover">
                <thead><tr><th>Nom</th><th>Email</th><th>Plan</th><th>Bots actifs</th></tr></thead>
                <tbody>
                {% for u in users %}
                <tr>
                    <td>{{ u.full_name or u.username }}</td>
                    <td>{{ u.email }}</td>
                    <td>{{ u.subscriptions|selectattr('is_active','eq',True)|map(attribute='plan.name')|join(', ') or 'Aucun' }}</td>
                    <td>{{ u.bots|selectattr('is_active','eq',True)|list|length }}</td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card p-3">
            <h5><i class="fas fa-pencil-alt" style="color:#f97316;"></i> Envoyer un message manuel</h5>
            <form>
                <div class="mb-2"><label style="color:#94a3b8;">Client</label><select class="form-select"><option>-- Tous --</option></select></div>
                <div class="mb-2"><label style="color:#94a3b8;">Titre</label><input class="form-control" placeholder="Alerte importante"></div>
                <div class="mb-2"><label style="color:#94a3b8;">Message</label><textarea class="form-control" rows="3">Votre bot vient de générer un signal...</textarea></div>
                <button class="btn btn-success"><i class="fas fa-paper-plane"></i> Envoyer</button>
            </form>
            <hr>
            <p class="text-muted small"><i class="fas fa-info-circle"></i> Les notifications automatiques sont générées quand un bot ouvre/ferme une position.</p>
        </div>
    </div>
</div>
{% endblock %}
"""), admin=admin, users=users, notifications=notifications, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/admin/generate_code', methods=['POST'])
@admin_required
def generate_code():
    plan_name = request.form['plan_name']
    duration = int(request.form['duration'])
    plan = Plan.query.filter_by(name=plan_name, duration_months=duration).first()
    if not plan:
        flash('❌ Plan invalide', 'danger')
        return redirect(url_for('admin_panel'))
    code = f"{plan_name.upper()}-{secrets.token_hex(6).upper()}"
    sub = Subscription(user_id=1, plan_id=plan.id, activation_code=code, is_active=False)
    db.session.add(sub)
    db.session.commit()
    flash(f'✅ Code généré avec succès : {code}', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/export_excel')
@admin_required
def export_excel():
    subs = Subscription.query.filter_by(is_active=True).all()
    data = []
    for sub in subs:
        user = db.session.get(User, sub.user_id)
        if user:
            data.append({
                'Nom': user.full_name or user.username,
                'Username': user.username,
                'Email': user.email,
                'Plan': sub.plan.name,
                'Durée (mois)': sub.plan.duration_months,
                'Prix (USD)': sub.plan.price_usd,
                'Commission %': sub.plan.commission_rate*100,
                'Début': sub.start_date.strftime('%Y-%m-%d'),
                'Fin': sub.end_date.strftime('%Y-%m-%d'),
                'Code': sub.activation_code
            })
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Clients')
    output.seek(0)
    return send_file(output, download_name='clients_abonnes.xlsx', as_attachment=True)

@app.route('/admin/export_full_clients')
@admin_required
def export_full_clients():
    users = User.query.all()
    data = []
    for user in users:
        sub = Subscription.query.filter_by(user_id=user.id, is_active=True).first()
        wallet = Wallet.query.filter_by(user_id=user.id).first()
        api_key = decrypt_api_key(user.api_key_binance) if user.api_key_binance else ''
        api_secret = decrypt_api_key(user.api_secret_binance) if user.api_secret_binance else ''
        data.append({
            'ID': user.id,
            'Nom complet': user.full_name or '',
            'Nom utilisateur': user.username,
            'Email': user.email,
            'Mot de passe (hash)': user.password,
            'API Key (déchiffrée)': api_key,
            'API Secret (déchiffré)': api_secret,
            'Plan': sub.plan.name if sub else 'Aucun',
            'Date début abo': sub.start_date.strftime('%Y-%m-%d') if sub else '',
            'Date fin abo': sub.end_date.strftime('%Y-%m-%d') if sub else '',
            'Solde wallet': wallet.balance if wallet else 0.0,
            'Admin': 'Oui' if user.is_admin else 'Non',
            'Langue': user.language,
            'Mode de trading': user.trading_mode if hasattr(user, 'trading_mode') else 'futures',
            'Date création': user.created_at.strftime('%Y-%m-%d %H:%M')
        })
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Clients_complet')
    output.seek(0)
    return send_file(output, download_name=f'clients_complet_{datetime.now().strftime("%Y%m%d")}.xlsx', as_attachment=True)

@app.route('/support', methods=['GET', 'POST'])
@login_required
def support():
    user = db.session.get(User, session['user_id'])
    if request.method == 'POST':
        question = request.form['question']
        faqs = FAQ.query.all()
        answer = None
        for faq in faqs:
            keywords = [k.strip().lower() for k in faq.keywords.split(',')]
            if any(k in question.lower() for k in keywords):
                answer = faq.response
                break
        if answer:
            q = ClientQuestion(user_id=user.id, question=question, answer=answer, answered_by='auto', answered_at=datetime.now(timezone.utc))
            db.session.add(q)
            db.session.commit()
            flash(f'🤖 Réponse automatique : {answer}', 'success')
        else:
            q = ClientQuestion(user_id=user.id, question=question)
            db.session.add(q)
            db.session.commit()
            flash('❓ Votre question a été enregistrée. Un admin vous répondra bientôt.', 'info')
        return redirect(url_for('support'))
    questions = ClientQuestion.query.filter_by(user_id=user.id).order_by(ClientQuestion.created_at.desc()).limit(20).all()
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-headset" style="color:#f97316;"></i> Support & FAQ</h2>
<div class="card p-4 mt-3">
    <form method="POST">
        <div class="mb-3"><label style="color:#94a3b8;">Votre question</label><textarea name="question" class="form-control" rows="3" required></textarea></div>
        <button class="btn btn-primary"><i class="fas fa-paper-plane"></i> Envoyer</button>
    </form>
</div>
<h4 class="mt-4">Historique de vos questions</h4>
<table class="table table-hover">
    <thead><tr><th>Question</th><th>Réponse</th><th>Date</th></tr></thead>
    <tbody>
    {% for q in questions %}
    <tr><td>{{ q.question }}</td><td>{% if q.answer %}{{ q.answer }}{% else %}<span class="text-warning">En attente...</span>{% endif %}</td><td>{{ q.created_at.strftime('%Y-%m-%d %H:%M') }}</td></tr>
    {% endfor %}
    </tbody>
</table>
{% endblock %}
"""), user=user, questions=questions, lang=user.language, t=t, LANGUAGES=LANGUAGES)

@app.route('/admin/questions')
@admin_required
def admin_questions():
    admin_user = db.session.get(User, session['user_id'])
    unanswered = ClientQuestion.query.filter_by(answer=None).all()
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-question-circle" style="color:#f97316;"></i> Questions des clients</h2>
<table class="table table-hover">
    <thead>
        <tr>
            <th><i class="fas fa-user" style="color:#f97316;"></i> Client</th>
            <th><i class="fas fa-question" style="color:#f97316;"></i> Question</th>
            <th><i class="fas fa-calendar" style="color:#f97316;"></i> Date</th>
            <th><i class="fas fa-cog" style="color:#f97316;"></i> Action</th>
        </tr>
    </thead>
    <tbody>
    {% for q in unanswered %}
    <tr>
        <td><strong>{{ q.user.full_name or q.user.username }}</strong></td>
        <td>{{ q.question }}</td>
        <td>{{ q.created_at.strftime('%Y-%m-%d %H:%M') }}</td>
        <td>
            <a href="{{ url_for('admin_answer_question', qid=q.id) }}" class="btn btn-sm btn-primary">
                <i class="fas fa-reply"></i> Répondre
            </a>
        </td>
    </tr>
    {% endfor %}
    </tbody>
</table>
{% endblock %}
"""), unanswered=unanswered, lang=admin_user.language, t=t, LANGUAGES=LANGUAGES)

@app.route('/admin/answer/<int:qid>', methods=['GET', 'POST'])
@admin_required
def admin_answer_question(qid):
    q = db.session.get(ClientQuestion, qid)
    if not q:
        flash('❌ Question introuvable', 'danger')
        return redirect(url_for('admin_questions'))
    if request.method == 'POST':
        q.answer = request.form['answer']
        q.answered_by = 'admin'
        q.answered_at = datetime.now(timezone.utc)
        db.session.commit()
        flash('✅ Réponse enregistrée et envoyée au client.', 'success')
        return redirect(url_for('admin_questions'))
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>Répondre à la question</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        body { background: #0a0a0a; font-family: 'Segoe UI', sans-serif; padding: 40px 0; }
        .container { max-width: 700px; }
        .card { background: #1a1a1a; border: 1px solid #f97316; border-radius: 16px; padding: 30px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }
        h2 { color: #ffffff; font-weight: 700; }
        h2 i { color: #f97316; }
        .btn-success { background: #22c55e; border: none; border-radius: 8px; }
        .btn-success:hover { background: #16a34a; }
        .btn-secondary { background: #2a2a2a; border: none; border-radius: 8px; color: #e2e8f0; }
        .btn-secondary:hover { background: #3a3a3a; color: #ffffff; }
        .form-control { background: #0a0a0a; border: 1px solid #2a2a2a; border-radius: 8px; color: #e2e8f0; }
        .form-control:focus { border-color: #f97316; box-shadow: 0 0 0 3px rgba(249,115,22,0.2); background: #0a0a0a; color: #ffffff; }
        strong { color: #f97316; }
        label { color: #94a3b8; }
    </style>
</head>
<body>
<div class="container mt-5">
    <div class="card">
        <h2><i class="fas fa-reply"></i> Répondre à {{ q.user.full_name or q.user.username }}</h2>
        <div class="card p-3 mb-3" style="background:#0a0a0a; border:1px solid #2a2a2a;">
            <strong>Question :</strong> {{ q.question }}
        </div>
        <form method="POST">
            <div class="mb-3">
                <label>Votre réponse</label>
                <textarea name="answer" class="form-control" rows="4" required></textarea>
            </div>
            <button type="submit" class="btn btn-success">
                <i class="fas fa-paper-plane"></i> Envoyer la réponse
            </button>
            <a href="{{ url_for('admin_questions') }}" class="btn btn-secondary">
                <i class="fas fa-arrow-left"></i> Retour
            </a>
        </form>
    </div>
</div>
</body>
</html>
""", q=q)

@app.route('/update')
@admin_required
def update_app():
    has_update, version = check_update()
    if has_update:
        if perform_update():
            flash(f'✅ Mise à jour vers la version {version} effectuée avec succès ! Redémarrez le serveur.', 'success')
        else:
            flash('❌ Échec de la mise à jour.', 'danger')
    else:
        flash('✅ Vous êtes déjà à jour.', 'info')
    return redirect(url_for('admin_panel'))

# ==================== DESIGN FACTORY ====================
from modules.design_factory.routes import design_factory_bp
app.register_blueprint(design_factory_bp)

# ==================== LANCEMENT ====================
if __name__ == '__main__':
    print("🚀 GOD MODE ACTIVÉ – IN-GINHO Ai Studio Pro ULTIMATE")
    print("🎨 DESIGN NOIR & ORANGE – Version Cyber Premium")
    print("📊 Export automatique toutes les heures: ✅ ACTIF")
    print("🔐 Chiffrement des clés API: ✅ ACTIF")
    print("🤖 Chatbot automatique: ✅ ACTIF")
    print("🔄 Système de mise à jour: ✅ ACTIF")
    print("🌍 5 langues: EN, FR, HI, PT, ZH")
    print("⚡ Modes: Futures, Spot, Hedge")
    print(f"💰 Montant par ordre: {INVEST_AMOUNT_USDT}$")
    print(f"📊 Levier: {LEVERAGE}x")
    print(f"🛡️ Positions max: {MAX_POSITIONS}")
    print("🌍 Adresse: http://127.0.0.1:5000")
    
    has_update, version = check_update()
    if has_update:
        print(f"🔄 Une nouvelle version ({version}) est disponible.")
    
    import os
port = int(os.environ.get('PORT', 5000))
app.run(debug=False, host='0.0.0.0', port=port)
