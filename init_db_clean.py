# -*- coding: utf-8 -*-
from app import app, db
from app import User, Plan, Subscription, Wallet, Transaction, BotInstance, Notification, FAQ, ClientQuestion

with app.app_context():
    db.create_all()
    print("Tables creees avec succes")
    
    # Ajouter l'admin si absent
    admin = User.query.filter_by(email='admin@admin.com').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@admin.com',
            password='admin123',
            is_admin=True,
            language='en',
            full_name='Administrateur',
            trading_mode='futures'
        )
        db.session.add(admin)
        db.session.commit()
        wallet = Wallet(user_id=admin.id, balance=1000.0)
        db.session.add(wallet)
        db.session.commit()
        print("Admin cree")
    
    # Ajouter des plans par défaut si vide
    if Plan.query.count() == 0:
        plans = [
            Plan(name='Trial', duration_months=0, price_usd=0.0, max_bots=1, commission_rate=0.0, features='1 bot'),
            Plan(name='Standard', duration_months=1, price_usd=29.0, max_bots=3, commission_rate=0.20, features='3 bots'),
            Plan(name='Pro', duration_months=1, price_usd=59.0, max_bots=5, commission_rate=0.15, features='5 bots'),
            Plan(name='Premium Pro', duration_months=1, price_usd=99.0, max_bots=10, commission_rate=0.10, features='10 bots'),
        ]
        db.session.add_all(plans)
        db.session.commit()
        print("Plans ajoutes")
    
    print("Base initialisee avec succes !")