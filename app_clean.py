import os
import secrets
import threading
import time
import random
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
import pandas as pd
from io import BytesIO
from binance.client import Client
from binance.exceptions import BinanceAPIException
import ta

# ==================== CONFIGURATION ====================
app = Flask(__name__)
app.secret_key = 'votre_cle_secrete_changez_moi_123456'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inginho.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

COMMISSIONS = {
    'Trial': 0.0,
    'Standard': 0.20,
    'Pro': 0.15,
    'Premium Pro': 0.10
}

# ==================== MODÈLES (AVEC NOUVEAUX CHAMPS) ====================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100), nullable=True)   # NOUVEAU : nom complet
    language = db.Column(db.String(5), default='en')
    is_admin = db.Column(db.Boolean, default=False)
    api_key_binance = db.Column(db.Text, nullable=True)
    api_secret_binance = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
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
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    wallet = db.relationship('Wallet', backref='transactions')

class BotInstance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100))
    symbol = db.Column(db.String(20))
    timeframe = db.Column(db.String(10), default='5m')      # NOUVEAU
    sl_percent = db.Column(db.Float, default=2.0)           # NOUVEAU
    tp_percent = db.Column(db.Float, default=4.0)           # NOUVEAU
    strategy = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    profit = db.Column(db.Float, default=0.0)
    entry_price = db.Column(db.Float, default=0.0)
    position_qty = db.Column(db.Float, default=0.0)
    # Champs pour l'affichage du signal (non persistants)
    signal_text = db.Column(db.String(50), default='⏳ WAIT')
    signal_color = db.Column(db.String(20), default='secondary')
    rsi_value = db.Column(db.String(10), default='N/A')
    user = db.relationship('User', backref='bots')

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    title = db.Column(db.String(100))
    message = db.Column(db.Text)
    type = db.Column(db.String(20))  # 'trade_alert', 'signal', 'warning'
    is_auto = db.Column(db.Boolean, default=True)
    is_sent = db.Column(db.Boolean, default=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='notifications')

# ==================== LANGUES ====================
LANGUAGES = {
    "en": {"name": "English", "app_title": "IN-GINHO Ai Studio Pro", "login": "Login", "register": "Register", "logout": "Logout", "dashboard": "Dashboard", "bots": "My Bots", "wallet": "Wallet", "subscription": "Subscription", "admin": "Admin Panel", "email": "Email", "password": "Password", "username": "Username", "language": "Language", "welcome": "Welcome", "balance": "Balance", "total_profit": "Total P&L", "active_bots": "Active Bots", "plan": "Plan", "expires": "Expires", "withdraw": "Withdraw", "amount": "Amount", "submit": "Submit", "generate_code": "Generate Code", "users": "Users", "export_excel": "Export to Excel", "commission_rate": "Commission Rate", "current_plan": "Current Plan", "upgrade": "Upgrade", "trial": "Trial", "standard": "Standard", "pro": "Pro", "premium": "Premium Pro", "duration": "Duration (months)", "price": "Price (USD)", "buy": "Buy / Activate", "activation_code": "Activation Code", "enter_code": "Enter your code", "activate": "Activate", "success": "Success", "error": "Error", "admin_only": "Admin area", "my_bots": "My Bots", "create_bot": "Create Bot", "symbol": "Symbol", "strategy": "Strategy", "start": "Start", "stop": "Stop", "transactions": "Transactions", "settings": "Settings", "api_keys": "API Keys", "profile": "Profile", "clients": "Clients"},
    "fr": {"name": "Français", "app_title": "IN-GINHO Ai Studio Pro", "login": "Connexion", "register": "Inscription", "logout": "Déconnexion", "dashboard": "Tableau de bord", "bots": "Mes Bots", "wallet": "Portefeuille", "subscription": "Abonnement", "admin": "Admin", "email": "Email", "password": "Mot de passe", "username": "Nom d'utilisateur", "language": "Langue", "welcome": "Bienvenue", "balance": "Solde", "total_profit": "P&L total", "active_bots": "Bots actifs", "plan": "Plan", "expires": "Expire le", "withdraw": "Retirer", "amount": "Montant", "submit": "Envoyer", "generate_code": "Générer un code", "users": "Utilisateurs", "export_excel": "Exporter en Excel", "commission_rate": "Taux de commission", "current_plan": "Plan actuel", "upgrade": "Mettre à niveau", "trial": "Essai", "standard": "Standard", "pro": "Pro", "premium": "Premium Pro", "duration": "Durée (mois)", "price": "Prix (USD)", "buy": "Acheter / Activer", "activation_code": "Code d'activation", "enter_code": "Entrez votre code", "activate": "Activer", "success": "Succès", "error": "Erreur", "admin_only": "Zone admin", "my_bots": "Mes Bots", "create_bot": "Créer un Bot", "symbol": "Symbole", "strategy": "Stratégie", "start": "Démarrer", "stop": "Arrêter", "transactions": "Transactions", "settings": "Paramètres", "api_keys": "Clés API", "profile": "Profil", "clients": "Clients"},
    "hi": {"name": "हिन्दी", "app_title": "IN-GINHO Ai Studio Pro", "login": "लॉगिन", "register": "पंजीकरण", "logout": "लॉगआउट", "dashboard": "डैशबोर्ड", "bots": "मेरे बॉट", "wallet": "वॉलेट", "subscription": "सदस्यता", "admin": "एडमिन", "email": "ईमेल", "password": "पासवर्ड", "username": "उपयोगकर्ता नाम", "language": "भाषा", "welcome": "स्वागत है", "balance": "शेष राशि", "total_profit": "कुल लाभ/हानि", "active_bots": "सक्रिय बॉट", "plan": "योजना", "expires": "समाप्ति", "withdraw": "निकासी", "amount": "राशि", "submit": "जमा करें", "generate_code": "कोड जनरेट करें", "users": "उपयोगकर्ता", "export_excel": "एक्सेल में निर्यात करें", "commission_rate": "कमीशन दर", "current_plan": "वर्तमान योजना", "upgrade": "अपग्रेड", "trial": "ट्रायल", "standard": "मानक", "pro": "प्रो", "premium": "प्रीमियम प्रो", "duration": "अवधि (महीने)", "price": "कीमत (USD)", "buy": "खरीदें / सक्रिय करें", "activation_code": "सक्रियण कोड", "enter_code": "अपना कोड दर्ज करें", "activate": "सक्रिय करें", "success": "सफलता", "error": "त्रुटि", "admin_only": "एडमिन क्षेत्र", "my_bots": "मेरे बॉट", "create_bot": "बॉट बनाएं", "symbol": "प्रतीक", "strategy": "रणनीति", "start": "शुरू करें", "stop": "रोकें", "transactions": "लेन-देन", "settings": "सेटिंग्स", "api_keys": "एपीआई कुंजी", "profile": "प्रोफ़ाइल", "clients": "ग्राहक"},
    "pt": {"name": "Português", "app_title": "IN-GINHO Ai Studio Pro", "login": "Entrar", "register": "Registrar", "logout": "Sair", "dashboard": "Painel", "bots": "Meus Bots", "wallet": "Carteira", "subscription": "Assinatura", "admin": "Admin", "email": "Email", "password": "Senha", "username": "Usuário", "language": "Idioma", "welcome": "Bem-vindo", "balance": "Saldo", "total_profit": "P&L Total", "active_bots": "Bots Ativos", "plan": "Plano", "expires": "Expira em", "withdraw": "Sacar", "amount": "Valor", "submit": "Enviar", "generate_code": "Gerar Código", "users": "Usuários", "export_excel": "Exportar para Excel", "commission_rate": "Taxa de Comissão", "current_plan": "Plano Atual", "upgrade": "Atualizar", "trial": "Teste", "standard": "Padrão", "pro": "Pro", "premium": "Premium Pro", "duration": "Duração (meses)", "price": "Preço (USD)", "buy": "Comprar / Ativar", "activation_code": "Código de Ativação", "enter_code": "Digite seu código", "activate": "Ativar", "success": "Sucesso", "error": "Erro", "admin_only": "Área admin", "my_bots": "Meus Bots", "create_bot": "Criar Bot", "symbol": "Símbolo", "strategy": "Estratégia", "start": "Iniciar", "stop": "Parar", "transactions": "Transações", "settings": "Configurações", "api_keys": "Chaves API", "profile": "Perfil", "clients": "Clientes"}
}

def t(key, lang='en', **kwargs):
    text = LANGUAGES.get(lang, LANGUAGES['en']).get(key, key)
    return text.format(**kwargs) if kwargs else text

# ==================== DÉCORATEURS ====================
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
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            return "Accès non autorisé", 403
        return f(*args, **kwargs)
    return decorated

# ==================== INIT DB ====================
def init_db():
    with app.app_context():
        db.create_all()
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
            admin = User(username='admin', email='admin@admin.com', password='admin123', is_admin=True, language='en', full_name='Administrateur')
            db.session.add(admin)
            db.session.commit()
            wallet = Wallet(user_id=admin.id, balance=1000.0)
            db.session.add(wallet)
            db.session.commit()
init_db()

# ==================== STRATÉGIE RSI + MACD + BB ====================
def get_signal(df):
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    macd = ta.trend.MACD(df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
    df['bb_mid'] = bb.bollinger_mavg()
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    rsi_bull = latest['rsi'] < 30
    macd_bull = latest['macd'] > latest['macd_signal'] and prev['macd'] <= prev['macd_signal']
    bb_bull = latest['close'] > latest['bb_mid']
    rsi_bear = latest['rsi'] > 70
    macd_bear = latest['macd'] < latest['macd_signal'] and prev['macd'] >= prev['macd_signal']
    bb_bear = latest['close'] < latest['bb_mid']
    if rsi_bull and macd_bull and bb_bull:
        return "BUY", latest['close'], latest['rsi'], "🐂 Bullish"
    elif rsi_bear and macd_bear and bb_bear:
        return "SELL", latest['close'], latest['rsi'], "🐻 Bearish"
    else:
        return "HOLD", latest['close'], latest['rsi'], "➖ Neutral"

# ==================== BOT SIMULATEUR (REEL + FALLBACK) ====================
def bot_simulator():
    with app.app_context():
        while True:
            time.sleep(10)
            try:
                bots = BotInstance.query.filter_by(is_active=True).all()
                for bot in bots:
                    user = User.query.get(bot.user_id)
                    # SIMULATION si pas de clés
                    if not user.api_key_binance or not user.api_secret_binance:
                        pct_change = random.uniform(-0.05, 0.08)
                        profit = bot.profit * pct_change if bot.profit != 0 else random.uniform(-10, 20)
                        bot.profit += profit
                        if profit > 0:
                            sub = Subscription.query.filter_by(user_id=user.id, is_active=True).first()
                            if sub and sub.plan:
                                rate = sub.plan.commission_rate
                                commission = profit * rate
                                admin_wallet = Wallet.query.filter_by(user_id=1).first()
                                if admin_wallet:
                                    admin_wallet.balance += commission
                                    tx = Transaction(wallet_id=admin_wallet.id, amount=commission, type='commission', description=f'Commission simulée {bot.name}')
                                    db.session.add(tx)
                                client_wallet = Wallet.query.filter_by(user_id=user.id).first()
                                if client_wallet:
                                    client_wallet.balance += (profit - commission)
                                    tx2 = Transaction(wallet_id=client_wallet.id, amount=(profit - commission), type='deposit', description=f'Profit simulé {bot.name}')
                                    db.session.add(tx2)
                            # Notification simulée
                            notif = Notification(user_id=user.id, title=f"💰 {bot.name} a fait un gain !", 
                                                message=f"Profit simulé de {profit:.2f} USDT sur {bot.symbol}", type='trade_alert', is_auto=True)
                            db.session.add(notif)
                        db.session.commit()
                        continue

                    # ---------- TRADING RÉEL ----------
                    try:
                        client = Client(user.api_key_binance, user.api_secret_binance, testnet=True)
                        klines = client.futures_klines(symbol=bot.symbol, interval=bot.timeframe, limit=100)
                        df = pd.DataFrame(klines)
                        df.columns = ['time','open','high','low','close','volume','close_time','quote_asset_volume','number_of_trades','taker_buy_base_asset_volume','taker_buy_quote_asset_volume','ignore']
                        df['close'] = pd.to_numeric(df['close'])
                        df['open'] = pd.to_numeric(df['open'])
                        df['high'] = pd.to_numeric(df['high'])
                        df['low'] = pd.to_numeric(df['low'])
                        
                        signal, price, rsi, sentiment = get_signal(df)
                        
                        # Mettre à jour les champs de signal pour l'affichage
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
                        
                        # Vérifier la position
                        positions = client.futures_position_information(symbol=bot.symbol)
                        current_pos_qty = 0
                        for pos in positions:
                            if float(pos['positionAmt']) != 0:
                                current_pos_qty = float(pos['positionAmt'])
                                break
                        
                        qty = 0.001
                        leverage = 5
                        
                        # EXÉCUTION DES ORDRES AVEC SL/TP DYNAMIQUES
                        if signal == "BUY" and current_pos_qty == 0 and bot.position_qty == 0:
                            client.futures_change_leverage(symbol=bot.symbol, leverage=leverage)
                            order = client.futures_create_order(symbol=bot.symbol, side='BUY', type='MARKET', quantity=qty)
                            entry_price = float(order['fills'][0]['price'])
                            sl_price = round(entry_price * (1 - bot.sl_percent / 100), 2)
                            tp_price = round(entry_price * (1 + bot.tp_percent / 100), 2)
                            client.futures_create_order(symbol=bot.symbol, side='SELL', type='STOP_MARKET', stopPrice=sl_price, quantity=qty, reduceOnly=True)
                            client.futures_create_order(symbol=bot.symbol, side='SELL', type='TAKE_PROFIT_MARKET', stopPrice=tp_price, quantity=qty, reduceOnly=True)
                            bot.entry_price = entry_price
                            bot.position_qty = qty
                            db.session.commit()
                            # Notification
                            notif = Notification(user_id=user.id, title=f"🟢 {bot.name} - ACHAT", 
                                                message=f"BUY {bot.symbol} à {entry_price} (SL: {sl_price}, TP: {tp_price})", type='trade_alert')
                            db.session.add(notif)
                            db.session.commit()
                            
                        elif signal == "SELL" and current_pos_qty == 0 and bot.position_qty == 0:
                            client.futures_change_leverage(symbol=bot.symbol, leverage=leverage)
                            order = client.futures_create_order(symbol=bot.symbol, side='SELL', type='MARKET', quantity=qty)
                            entry_price = float(order['fills'][0]['price'])
                            sl_price = round(entry_price * (1 + bot.sl_percent / 100), 2)
                            tp_price = round(entry_price * (1 - bot.tp_percent / 100), 2)
                            client.futures_create_order(symbol=bot.symbol, side='BUY', type='STOP_MARKET', stopPrice=sl_price, quantity=qty, reduceOnly=True)
                            client.futures_create_order(symbol=bot.symbol, side='BUY', type='TAKE_PROFIT_MARKET', stopPrice=tp_price, quantity=qty, reduceOnly=True)
                            bot.entry_price = entry_price
                            bot.position_qty = -qty
                            db.session.commit()
                            notif = Notification(user_id=user.id, title=f"🔴 {bot.name} - VENTE", 
                                                message=f"SELL {bot.symbol} à {entry_price} (SL: {sl_price}, TP: {tp_price})", type='trade_alert')
                            db.session.add(notif)
                            db.session.commit()
                            
                        # Vérifier si position fermée (calcul du profit)
                        if current_pos_qty == 0 and bot.position_qty != 0:
                            ticker = client.futures_symbol_ticker(symbol=bot.symbol)
                            current_price = float(ticker['price'])
                            if bot.entry_price > 0:
                                if bot.position_qty > 0:
                                    profit = (current_price - bot.entry_price) * bot.position_qty
                                else:
                                    profit = (bot.entry_price - current_price) * abs(bot.position_qty)
                                
                                sub = Subscription.query.filter_by(user_id=user.id, is_active=True).first()
                                if sub and sub.plan and profit > 0:
                                    rate = sub.plan.commission_rate
                                    commission = profit * rate
                                    admin_wallet = Wallet.query.filter_by(user_id=1).first()
                                    if admin_wallet:
                                        admin_wallet.balance += commission
                                        tx = Transaction(wallet_id=admin_wallet.id, amount=commission, type='commission', description=f'Commission réelle {bot.name}')
                                        db.session.add(tx)
                                    client_wallet = Wallet.query.filter_by(user_id=user.id).first()
                                    if client_wallet:
                                        client_wallet.balance += (profit - commission)
                                        tx2 = Transaction(wallet_id=client_wallet.id, amount=(profit - commission), type='deposit', description=f'Profit réel {bot.name}')
                                        db.session.add(tx2)
                                    notif = Notification(user_id=user.id, title=f"💰 {bot.name} - Profit", 
                                                        message=f"Profit de {profit:.2f} USDT sur {bot.symbol}", type='trade_alert')
                                    db.session.add(notif)
                                bot.profit += profit
                                bot.entry_price = 0
                                bot.position_qty = 0
                                db.session.commit()
                    except Exception as e:
                        print(f"Erreur {bot.name}: {e}")
            except Exception as e:
                print(f"Erreur boucle: {e}")

thread = threading.Thread(target=bot_simulator, daemon=True)
thread.start()

# ==================== TEMPLATE DE BASE (GLASSMORPHISM) ====================
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
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        * { font-family: 'Inter', sans-serif; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
            min-height: 100vh;
            color: #e2e8f0;
        }
        .sidebar {
            background: rgba(15, 23, 42, 0.85) !important;
            backdrop-filter: blur(20px);
            border-right: 1px solid rgba(255, 255, 255, 0.05);
            box-shadow: 4px 0 30px rgba(0, 0, 0, 0.3);
            min-height: 100vh;
        }
        .sidebar h4 { color: #38bdf8 !important; text-shadow: 0 0 20px rgba(56, 189, 248, 0.3); font-weight: 700; letter-spacing: -0.5px; }
        .nav-link { color: #94a3b8 !important; font-weight: 500; border-radius: 12px; padding: 10px 16px; margin: 2px 0; transition: all 0.3s ease; }
        .nav-link:hover { background: rgba(56, 189, 248, 0.1) !important; color: #38bdf8 !important; transform: translateX(4px); }
        .nav-link.text-danger:hover { background: rgba(239, 68, 68, 0.15) !important; color: #f87171 !important; }
        .card {
            background: rgba(255, 255, 255, 0.05) !important;
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            color: #e2e8f0;
        }
        .card:hover { transform: translateY(-2px); box-shadow: 0 12px 48px rgba(0, 0, 0, 0.3); }
        .card h5 { color: #94a3b8; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; font-size: 0.75rem; }
        .card h3 { color: #ffffff; font-weight: 700; }
        .btn-primary { background: linear-gradient(135deg, #3b82f6, #2563eb); border: none; border-radius: 10px; padding: 10px 24px; font-weight: 600; transition: all 0.3s ease; color: #fff; }
        .btn-primary:hover { transform: scale(1.02); box-shadow: 0 8px 24px rgba(59, 130, 246, 0.4); background: linear-gradient(135deg, #60a5fa, #3b82f6); }
        .btn-success { background: linear-gradient(135deg, #22c55e, #16a34a); border: none; border-radius: 10px; font-weight: 600; transition: all 0.3s ease; color: #fff; }
        .btn-success:hover { transform: scale(1.02); box-shadow: 0 8px 24px rgba(34, 197, 94, 0.4); }
        .btn-danger { background: linear-gradient(135deg, #ef4444, #dc2626); border: none; border-radius: 10px; font-weight: 600; transition: all 0.3s ease; color: #fff; }
        .btn-danger:hover { transform: scale(1.02); box-shadow: 0 8px 24px rgba(239, 68, 68, 0.4); }
        .btn-warning { background: linear-gradient(135deg, #f59e0b, #d97706); border: none; border-radius: 10px; font-weight: 600; color: #0f172a; transition: all 0.3s ease; }
        .btn-warning:hover { transform: scale(1.02); box-shadow: 0 8px 24px rgba(245, 158, 11, 0.4); }
        .table { background: transparent; color: #e2e8f0; }
        .table thead th { background: rgba(255, 255, 255, 0.03); color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.5px; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .table tbody tr { border-bottom: 1px solid rgba(255,255,255,0.03); transition: background 0.2s ease; }
        .table tbody tr:hover { background: rgba(255, 255, 255, 0.03); }
        .form-control, .form-select {
            background: rgba(255, 255, 255, 0.05) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-radius: 10px !important;
            color: #e2e8f0 !important;
            backdrop-filter: blur(8px);
            padding: 12px 16px;
        }
        .form-control:focus, .form-select:focus { border-color: #3b82f6 !important; box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2) !important; background: rgba(255, 255, 255, 0.08) !important; }
        .form-control::placeholder { color: #64748b; }
        .badge { font-weight: 600; padding: 6px 14px; border-radius: 20px; font-size: 0.75rem; }
        .bg-success { background: linear-gradient(135deg, #22c55e, #16a34a) !important; }
        .bg-danger { background: linear-gradient(135deg, #ef4444, #dc2626) !important; }
        .bg-warning { background: linear-gradient(135deg, #f59e0b, #d97706) !important; color: #0f172a; }
        .bg-secondary { background: #475569 !important; }
        .bg-info { background: linear-gradient(135deg, #3b82f6, #2563eb) !important; }
        .text-glow { color: #38bdf8 !important; text-shadow: 0 0 30px rgba(56, 189, 248, 0.2); }
        .text-success { color: #4ade80 !important; }
        .text-danger { color: #f87171 !important; }
        .text-warning { color: #fbbf24 !important; }
        .text-muted { color: #94a3b8 !important; }
        hr { border-color: rgba(255,255,255,0.05); }
        select option { background: #1e293b; color: #e2e8f0; }
        .alert-info { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.2); color: #93c5fd; border-radius: 12px; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: rgba(255,255,255,0.02); }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }
    </style>
</head>
<body>
    <div class="container-fluid">
        <div class="row">
            <nav class="col-md-2 d-md-block sidebar p-3">
                <h4 class="text-glow mb-4"><i class="fas fa-robot"></i> {{ t('app_title', lang=lang) }}</h4>
                <ul class="nav flex-column">
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('dashboard') }}"><i class="fas fa-chart-pie"></i> {{ t('dashboard', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('manage_bots') }}"><i class="fas fa-robot"></i> {{ t('bots', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('wallet_view') }}"><i class="fas fa-wallet"></i> {{ t('wallet', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('subscription_view') }}"><i class="fas fa-crown"></i> {{ t('subscription', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('settings') }}"><i class="fas fa-key"></i> {{ t('api_keys', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('profile') }}"><i class="fas fa-user"></i> {{ t('profile', lang=lang) }}</a></li>
                    {% if user and user.is_admin %}<li class="nav-item"><a class="nav-link" href="{{ url_for('admin_panel') }}"><i class="fas fa-shield-alt"></i> {{ t('admin', lang=lang) }}</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_clients') }}"><i class="fas fa-users"></i> {{ t('clients', lang=lang) }}</a></li>{% endif %}
                    <li class="nav-item"><a class="nav-link text-danger" href="{{ url_for('logout') }}"><i class="fas fa-sign-out-alt"></i> {{ t('logout', lang=lang) }}</a></li>
                </ul>
                <hr>
                <div class="mt-3">
                    <label><i class="fas fa-globe"></i> {{ t('language', lang=lang) }}</label>
                    <select class="form-select form-select-sm" onchange="window.location='/set_language/'+this.value">
                        {% for code, lang_data in LANGUAGES.items() %}
                        <option value="{{ code }}" {% if lang == code %}selected{% endif %}>{{ lang_data.name }}</option>
                        {% endfor %}
                    </select>
                </div>
            </nav>
            <main class="col-md-10 ms-sm-auto px-md-4 py-4">
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
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/set_language/<lang>')
def set_language(lang):
    if lang in LANGUAGES:
        session['lang'] = lang
        if 'user_id' in session:
            user = User.query.get(session['user_id'])
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
            return redirect(url_for('dashboard'))
        return "Identifiants incorrects"
    lang = session.get('lang', 'en')
    return render_template_string("""
<!DOCTYPE html><html><head><title>Login</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>body{background:linear-gradient(135deg,#0f172a,#1e293b);display:flex;justify-content:center;align-items:center;height:100vh;font-family:'Inter',sans-serif;}
.card{background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.08);border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.3);padding:30px;width:400px;color:#e2e8f0;}
.btn-primary{background:linear-gradient(135deg,#3b82f6,#2563eb);border:none;border-radius:10px;font-weight:600;}
.btn-primary:hover{transform:scale(1.02);box-shadow:0 8px 24px rgba(59,130,246,0.4);}
h3{color:#38bdf8;font-weight:700;text-shadow:0 0 30px rgba(56,189,248,0.2);}
.form-control{background:rgba(255,255,255,0.05)!important;border:1px solid rgba(255,255,255,0.1)!important;border-radius:10px!important;color:#e2e8f0!important;}
.form-control:focus{border-color:#3b82f6!important;box-shadow:0 0 0 3px rgba(59,130,246,0.2)!important;}
a{color:#38bdf8;text-decoration:none;}
a:hover{color:#60a5fa;}
</style></head><body>
<div class="card"><h3 class="text-center"><i class="fas fa-robot"></i> {{ t('app_title', lang=lang) }}</h3>
<form method="POST"><div class="mb-3"><label><i class="fas fa-envelope"></i> {{ t('email', lang=lang) }}</label><input name="email" class="form-control" required></div>
<div class="mb-3"><label><i class="fas fa-lock"></i> {{ t('password', lang=lang) }}</label><input name="password" type="password" class="form-control" required></div>
<button class="btn btn-primary w-100"><i class="fas fa-sign-in-alt"></i> {{ t('login', lang=lang) }}</button></form>
<p class="mt-3 text-center"><a href="{{ url_for('register') }}">{{ t('register', lang=lang) }}</a></p>
<p class="mt-2 text-center"><small>Admin: admin@admin.com / admin123</small></p></div></body></html>""", lang=lang, t=t)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if User.query.filter_by(email=request.form['email']).first():
            return "Email déjà utilisé"
        user = User(username=request.form['username'], email=request.form['email'], password=request.form['password'], language='en', full_name=request.form.get('full_name', ''))
        db.session.add(user)
        db.session.commit()
        wallet = Wallet(user_id=user.id, balance=0.0)
        db.session.add(wallet)
        db.session.commit()
        return redirect(url_for('login'))
    lang = session.get('lang', 'en')
    return render_template_string("""
<!DOCTYPE html><html><head><title>Register</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>body{background:linear-gradient(135deg,#0f172a,#1e293b);display:flex;justify-content:center;align-items:center;height:100vh;font-family:'Inter',sans-serif;}
.card{background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.08);border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.3);padding:30px;width:400px;color:#e2e8f0;}
.btn-success{background:linear-gradient(135deg,#22c55e,#16a34a);border:none;border-radius:10px;font-weight:600;}
.btn-success:hover{transform:scale(1.02);box-shadow:0 8px 24px rgba(34,197,94,0.4);}
h3{color:#4ade80;font-weight:700;text-shadow:0 0 30px rgba(74,222,128,0.2);}
.form-control{background:rgba(255,255,255,0.05)!important;border:1px solid rgba(255,255,255,0.1)!important;border-radius:10px!important;color:#e2e8f0!important;}
.form-control:focus{border-color:#22c55e!important;box-shadow:0 0 0 3px rgba(34,197,94,0.2)!important;}
a{color:#4ade80;text-decoration:none;}
a:hover{color:#86efac;}
</style></head><body>
<div class="card"><h3 class="text-center"><i class="fas fa-user-plus"></i> {{ t('register', lang=lang) }}</h3>
<form method="POST">
<div class="mb-3"><label><i class="fas fa-user"></i> {{ t('username', lang=lang) }}</label><input name="username" class="form-control" required></div>
<div class="mb-3"><label><i class="fas fa-user-tag"></i> Nom complet</label><input name="full_name" class="form-control" placeholder="Votre nom"></div>
<div class="mb-3"><label><i class="fas fa-envelope"></i> {{ t('email', lang=lang) }}</label><input name="email" class="form-control" required></div>
<div class="mb-3"><label><i class="fas fa-lock"></i> {{ t('password', lang=lang) }}</label><input name="password" type="password" class="form-control" required></div>
<button class="btn btn-success w-100"><i class="fas fa-check"></i> {{ t('register', lang=lang) }}</button></form>
<p class="mt-3 text-center"><a href="{{ url_for('login') }}">{{ t('login', lang=lang) }}</a></p></div></body></html>""", lang=lang, t=t)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    lang = user.language
    wallet = Wallet.query.filter_by(user_id=user.id).first()
    subs = Subscription.query.filter_by(user_id=user.id, is_active=True).first()
    bots = BotInstance.query.filter_by(user_id=user.id).all()
    total_profit = sum(b.profit for b in bots)
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-chart-pie"></i> {{ t('welcome', lang=lang) }}, {{ user.full_name or user.username }}!</h2>
<div class="row mt-4">
    <div class="col-md-3"><div class="card p-3"><h5><i class="fas fa-coins"></i> {{ t('balance', lang=lang) }}</h5><h3>${{ "%.2f"|format(wallet.balance if wallet else 0) }}</h3></div></div>
    <div class="col-md-3"><div class="card p-3"><h5><i class="fas fa-chart-line"></i> {{ t('total_profit', lang=lang) }}</h5><h3 class="{% if total_profit > 0 %}text-success{% else %}text-danger{% endif %}">${{ "%.2f"|format(total_profit) }}</h3></div></div>
    <div class="col-md-3"><div class="card p-3"><h5><i class="fas fa-microchip"></i> {{ t('active_bots', lang=lang) }}</h5><h3>{{ bots|selectattr('is_active','eq',True)|list|length }}</h3></div></div>
    <div class="col-md-3"><div class="card p-3"><h5><i class="fas fa-crown"></i> {{ t('plan', lang=lang) }}</h5><h3>{{ subs.plan.name if subs else 'Aucun' }}</h3></div></div>
</div>
<div class="row mt-4"><div class="col-md-12"><div class="card p-3"><canvas id="pnlChart" height="100"></canvas></div></div></div>
<script>
const ctx = document.getElementById('pnlChart').getContext('2d');
new Chart(ctx, { type: 'line', data: { labels: ['Jan','Fev','Mar','Avr','Mai','Juin'], datasets: [{ label: 'P&L', data: [10,20,15,30,25,{{ total_profit|round(2) }}], borderColor: '#38bdf8', tension: 0.1, fill: true, backgroundColor: 'rgba(56,189,248,0.1)' }] }, options: { responsive: true, plugins: { legend: { labels: { color: '#e2e8f0' } } } } });
</script>
{% endblock %}
"""), user=user, wallet=wallet, subs=subs, bots=bots, total_profit=total_profit, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        user.full_name = request.form['full_name']
        db.session.commit()
        return redirect(url_for('profile'))
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-user"></i> Mon Profil</h2>
<div class="card p-4 mt-3">
    <form method="POST">
        <div class="mb-3"><label><i class="fas fa-user-tag"></i> Nom complet</label><input name="full_name" class="form-control" value="{{ user.full_name or '' }}" placeholder="Votre nom"></div>
        <div class="mb-3"><label><i class="fas fa-user"></i> Nom d'utilisateur</label><input class="form-control" value="{{ user.username }}" disabled></div>
        <div class="mb-3"><label><i class="fas fa-envelope"></i> Email</label><input class="form-control" value="{{ user.email }}" disabled></div>
        <button class="btn btn-primary"><i class="fas fa-save"></i> Mettre à jour</button>
    </form>
</div>
{% endblock %}
"""), user=user)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    user = User.query.get(session['user_id'])
    lang = user.language
    if request.method == 'POST':
        user.api_key_binance = request.form['api_key']
        user.api_secret_binance = request.form['api_secret']
        db.session.commit()
        return redirect(url_for('settings'))
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-key"></i> {{ t('api_keys', lang=lang) }}</h2>
<div class="card p-4 mt-3">
    <form method="POST">
        <div class="mb-3"><label><i class="fas fa-key"></i> API Key Binance</label><input name="api_key" class="form-control" value="{{ user.api_key_binance or '' }}"></div>
        <div class="mb-3"><label><i class="fas fa-lock"></i> API Secret</label><input name="api_secret" type="password" class="form-control" value="{{ user.api_secret_binance or '' }}"></div>
        <button class="btn btn-primary"><i class="fas fa-save"></i> {{ t('submit', lang=lang) }}</button>
    </form>
    <p class="text-warning mt-3"><i class="fas fa-exclamation-triangle"></i> Utilisez des clés TESTNET pour tester.</p>
</div>
{% endblock %}
"""), user=user, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/bots', methods=['GET', 'POST'])
@login_required
def manage_bots():
    user = User.query.get(session['user_id'])
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
        return redirect(url_for('manage_bots'))
    
    bots = BotInstance.query.filter_by(user_id=user.id).all()
    # Mise à jour des signaux pour chaque bot
    for bot in bots:
        try:
            if user.api_key_binance and user.api_secret_binance:
                client = Client(user.api_key_binance, user.api_secret_binance, testnet=True)
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
        except:
            bot.signal_text = "⚠️ Erreur"
            bot.signal_color = "warning"
            bot.rsi_value = "N/A"
    
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-robot"></i> {{ t('my_bots', lang=lang) }}</h2>
<div class="card p-4 mt-3">
    <form method="POST">
        <div class="row g-3">
            <div class="col-md-2"><label>Nom</label><input name="name" class="form-control" placeholder="Mon Bot" required></div>
            <div class="col-md-2"><label>Symbole</label><select name="symbol" class="form-select"><option value="BTCUSDT">BTCUSDT</option><option value="ETHUSDT" selected>ETHUSDT</option><option value="SOLUSDT">SOLUSDT</option><option value="BNBUSDT">BNBUSDT</option><option value="XRPUSDT">XRPUSDT</option><option value="ADAUSDT">ADAUSDT</option><option value="DOGEUSDT">DOGEUSDT</option></select></div>
            <div class="col-md-2"><label>Timeframe</label><select name="timeframe" class="form-select"><option value="1m">1 min</option><option value="5m" selected>5 min</option><option value="15m">15 min</option><option value="1h">1h</option></select></div>
            <div class="col-md-2"><label>SL %</label><input name="sl_percent" class="form-control" type="number" step="0.1" value="2.0"></div>
            <div class="col-md-2"><label>TP %</label><input name="tp_percent" class="form-control" type="number" step="0.1" value="4.0"></div>
            <div class="col-md-1 d-flex align-items-end"><button class="btn btn-primary w-100"><i class="fas fa-plus"></i></button></div>
        </div>
    </form>
</div>
<table class="table table-hover mt-4">
    <tr><th>Nom</th><th>Symbole</th><th>TF</th><th>SL</th><th>TP</th><th>Signal</th><th>RSI</th><th>Profit</th><th>Statut</th><th>Action</th></tr>
    {% for bot in bots %}
    <tr>
        <td>{{ bot.name }}</td>
        <td>{{ bot.symbol }}</td>
        <td>{{ bot.timeframe }}</td>
        <td>{{ bot.sl_percent }}%</td>
        <td>{{ bot.tp_percent }}%</td>
        <td><span class="badge bg-{{ bot.signal_color }}">{{ bot.signal_text }}</span></td>
        <td>{{ bot.rsi_value }}</td>
        <td class="{% if bot.profit > 0 %}text-success{% else %}text-danger{% endif %}">${{ "%.2f"|format(bot.profit) }}</td>
        <td>{{ 'Actif' if bot.is_active else 'Inactif' }}</td>
        <td><a href="{{ url_for('bot_toggle', bot_id=bot.id) }}" class="btn btn-sm {% if bot.is_active %}btn-danger{% else %}btn-success{% endif %}"><i class="fas {% if bot.is_active %}fa-stop{% else %}fa-play{% endif %}"></i> {{ t('stop' if bot.is_active else 'start', lang=lang) }}</a></td>
    </tr>
    {% endfor %}
</table>
<div class="alert alert-info"><i class="fas fa-lightbulb"></i> <strong>Signal</strong> : 🔥 BUY = RSI < 30 + MACD Bullish | 📉 SELL = RSI > 70 + MACD Bearish | ⏳ WAIT = Neutre</div>
{% endblock %}
"""), user=user, bots=bots, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/bot_toggle/<int:bot_id>')
@login_required
def bot_toggle(bot_id):
    bot = BotInstance.query.get(bot_id)
    if bot and bot.user_id == session['user_id']:
        bot.is_active = not bot.is_active
        db.session.commit()
    return redirect(url_for('manage_bots'))

@app.route('/wallet', methods=['GET', 'POST'])
@login_required
def wallet_view():
    user = User.query.get(session['user_id'])
    lang = user.language
    wallet = Wallet.query.filter_by(user_id=user.id).first()
    if request.method == 'POST':
        amount = float(request.form['amount'])
        if amount <= wallet.balance:
            wallet.balance -= amount
            tx = Transaction(wallet_id=wallet.id, amount=-amount, type='withdraw', description='Retrait')
            db.session.add(tx)
            db.session.commit()
            return redirect(url_for('wallet_view'))
    transactions = Transaction.query.filter_by(wallet_id=wallet.id).order_by(Transaction.timestamp.desc()).limit(20).all()
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-wallet"></i> {{ t('wallet', lang=lang) }}</h2>
<div class="card p-4 mt-3">
    <h3><i class="fas fa-coins"></i> {{ t('balance', lang=lang) }}: ${{ "%.2f"|format(wallet.balance) }}</h3>
    <form method="POST" class="row mt-3">
        <div class="col-4"><input name="amount" class="form-control" placeholder="{{ t('amount', lang=lang) }}" step="0.01" min="0"></div>
        <div class="col-2"><button class="btn btn-warning"><i class="fas fa-hand-holding-usd"></i> {{ t('withdraw', lang=lang) }}</button></div>
    </form>
</div>
<h4 class="mt-4"><i class="fas fa-list"></i> {{ t('transactions', lang=lang) }}</h4>
<table class="table table-hover">
    <tr><th>Type</th><th>Montant</th><th>Description</th><th>Date</th></tr>
    {% for tx in transactions %}
    <tr><td>{{ tx.type }}</td><td class="{% if tx.amount > 0 %}text-success{% else %}text-danger{% endif %}">${{ "%.2f"|format(tx.amount) }}</td><td>{{ tx.description }}</td><td>{{ tx.timestamp.strftime('%Y-%m-%d %H:%M') }}</td></tr>
    {% endfor %}
</table>
{% endblock %}
"""), user=user, wallet=wallet, transactions=transactions, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/subscription', methods=['GET', 'POST'])
@login_required
def subscription_view():
    user = User.query.get(session['user_id'])
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
                sub.start_date = datetime.utcnow()
                sub.end_date = datetime.utcnow() + timedelta(days=sub.plan.duration_months*30)
                db.session.commit()
                return redirect(url_for('subscription_view'))
        elif action == 'buy_plan':
            plan = Plan.query.get(int(request.form['plan_id']))
            code = f"{plan.name.upper()}-{secrets.token_hex(4).upper()}"
            new_sub = Subscription(user_id=user.id, plan_id=plan.id, activation_code=code, start_date=datetime.utcnow(), end_date=datetime.utcnow()+timedelta(days=plan.duration_months*30))
            db.session.add(new_sub)
            db.session.commit()
            return redirect(url_for('subscription_view'))
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-crown"></i> {{ t('subscription', lang=lang) }}</h2>
{% if subs %}
<div class="card p-3 mt-3"><h5><i class="fas fa-check-circle text-success"></i> {{ t('current_plan', lang=lang) }}: {{ subs.plan.name }}</h5><p><i class="fas fa-calendar-alt"></i> {{ t('expires', lang=lang) }}: {{ subs.end_date.strftime('%Y-%m-%d') }}</p></div>
{% endif %}
<div class="card p-4 mt-3">
    <h5><i class="fas fa-qrcode"></i> {{ t('activation_code', lang=lang) }}</h5>
    <form method="POST"><input type="hidden" name="action" value="activate_code">
        <div class="row"><div class="col-6"><input name="code" class="form-control" placeholder="{{ t('enter_code', lang=lang) }}"></div>
        <div class="col-2"><button class="btn btn-success"><i class="fas fa-check"></i> {{ t('activate', lang=lang) }}</button></div></div>
    </form>
</div>
<h4 class="mt-4"><i class="fas fa-list"></i> {{ t('plans', lang=lang) }}</h4>
<table class="table table-hover">
    <tr><th>Plan</th><th>Durée</th><th>Prix</th><th>Commission</th><th>Action</th></tr>
    {% for plan in plans %}
    <tr>
        <td><strong>{{ plan.name }}</strong></td>
        <td>{{ plan.duration_months }} mois</td>
        <td>${{ plan.price_usd }}</td>
        <td><span class="badge bg-info">{{ (plan.commission_rate * 100)|int }}%</span></td>
        <td><form method="POST"><input type="hidden" name="action" value="buy_plan"><input type="hidden" name="plan_id" value="{{ plan.id }}"><button class="btn btn-sm btn-primary"><i class="fas fa-shopping-cart"></i> {{ t('buy', lang=lang) }}</button></form></td>
    </tr>
    {% endfor %}
</table>
{% endblock %}
"""), user=user, subs=subs, plans=plans, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/admin')
@admin_required
def admin_panel():
    admin = User.query.get(session['user_id'])
    lang = admin.language
    users = User.query.all()
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-shield-alt"></i> {{ t('admin', lang=lang) }}</h2>
<div class="row mt-3">
    <div class="col-md-6">
        <div class="card p-3"><h5><i class="fas fa-qrcode"></i> {{ t('generate_code', lang=lang) }}</h5>
        <form method="POST" action="{{ url_for('generate_code') }}">
            <select name="plan_name" class="form-select"><option value="Standard">Standard</option><option value="Pro">Pro</option><option value="Premium Pro">Premium Pro</option></select>
            <select name="duration" class="form-select mt-2"><option value="1">1 mois</option><option value="3">3 mois</option><option value="6">6 mois</option><option value="12">12 mois</option></select>
            <button class="btn btn-success mt-2"><i class="fas fa-plus"></i> {{ t('generate_code', lang=lang) }}</button>
        </form>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card p-3"><h5><i class="fas fa-file-excel"></i> {{ t('export_excel', lang=lang) }}</h5>
        <a href="{{ url_for('export_excel') }}" class="btn btn-primary"><i class="fas fa-download"></i> {{ t('export_excel', lang=lang) }}</a>
        </div>
    </div>
</div>
<h4 class="mt-4"><i class="fas fa-users"></i> {{ t('users', lang=lang) }}</h4>
<table class="table table-hover"><tr><th>ID</th><th>Nom</th><th>Username</th><th>Email</th><th>Admin</th></tr>{% for u in users %}<tr><td>{{ u.id }}</td><td>{{ u.full_name or '—' }}</td><td>{{ u.username }}</td><td>{{ u.email }}</td><td>{{ 'Oui' if u.is_admin else 'Non' }}</td></tr>{% endfor %}</table>
{% endblock %}
"""), admin=admin, users=users, lang=lang, t=t, LANGUAGES=LANGUAGES)

@app.route('/admin/clients')
@admin_required
def admin_clients():
    admin = User.query.get(session['user_id'])
    lang = admin.language
    users = User.query.filter_by(is_admin=False).all()
    notifications = Notification.query.order_by(Notification.created_at.desc()).limit(50).all()
    return render_template_string(BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2><i class="fas fa-users"></i> Gestion des Clients</h2>
<div class="row mt-3">
    <div class="col-md-12">
        <div class="card p-3">
            <h5><i class="fas fa-bell"></i> Notifications en attente</h5>
            <table class="table table-hover">
                <tr><th>Client</th><th>Message</th><th>Type</th><th>Auto</th><th>Action</th></tr>
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
            </table>
        </div>
    </div>
</div>
<div class="row mt-3">
    <div class="col-md-6">
        <div class="card p-3">
            <h5><i class="fas fa-list"></i> Clients ({{ users|length }})</h5>
            <table class="table table-hover">
                <tr><th>Nom</th><th>Email</th><th>Plan</th><th>Bots actifs</th></tr>
                {% for u in users %}
                <tr>
                    <td>{{ u.full_name or u.username }}</td>
                    <td>{{ u.email }}</td>
                    <td>{{ u.subscriptions|selectattr('is_active','eq',True)|map(attribute='plan.name')|join(', ') or 'Aucun' }}</td>
                    <td>{{ u.bots|selectattr('is_active','eq',True)|list|length }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card p-3">
            <h5><i class="fas fa-pencil-alt"></i> Envoyer un message manuel</h5>
            <form>
                <div class="mb-2"><label>Client</label><select class="form-select"><option>-- Tous --</option></select></div>
                <div class="mb-2"><label>Titre</label><input class="form-control" placeholder="Alerte importante"></div>
                <div class="mb-2"><label>Message</label><textarea class="form-control" rows="3">Votre bot vient de générer un signal...</textarea></div>
                <button class="btn btn-success"><i class="fas fa-paper-plane"></i> Envoyer</button>
            </form>
            <hr>
            <p class="text-muted small"><i class="fas fa-info-circle"></i> Les notifications automatiques sont générées quand un bot ouvre/ferme une position. Vous pouvez les modifier avant envoi.</p>
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
        return "Plan invalide", 400
    code = f"{plan_name.upper()}-{secrets.token_hex(6).upper()}"
    sub = Subscription(user_id=1, plan_id=plan.id, activation_code=code, is_active=False)
    db.session.add(sub)
    db.session.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/export_excel')
@admin_required
def export_excel():
    subs = Subscription.query.filter_by(is_active=True).all()
    data = []
    for sub in subs:
        user = User.query.get(sub.user_id)
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

# ==================== LANCEMENT ====================
if __name__ == '__main__':
    print("🚀 Démarrage de IN-GINHO Ai Studio Pro (version complète)")
    app.run(debug=True, host='0.0.0.0', port=5000)