#This file is part paypalgateway blueprint for Flask.
#The COPYRIGHT file at the top level of this repository contains 
#the full copyright notices and license terms.
from flask import Blueprint, request, render_template, flash, current_app, g, \
    session, abort, url_for, redirect
from flask.ext.babel import gettext as _
from galatea.tryton import tryton
from galatea.csrf import csrf
from decimal import Decimal

paypalgateway = Blueprint('paypalgateway', __name__, template_folder='templates')

SHOP = current_app.config.get('TRYTON_SALE_SHOP')

Shop = tryton.pool.get('sale.shop')
GatewayTransaction = tryton.pool.get('account.payment.gateway.transaction')

PAYPAL_URL = "https://www.paypal.com/cgi-bin/webscr"
PAYPAL_SANDBOX_URL = "https://www.sandbox.paypal.com/cgi-bin/webscr"

# DOC https://developer.paypal.com/docs/classic/ipn/integration-guide/IPNandPDTVariables/
PAYPAL_RESPONSES_CANCEL = ['Canceled_Reversal', 'Denied', 'Expired', 'Voided']
PAYPAL_RESPONSES_PENDING = ['Pending']
PAYPAL_RESPONSES_FAILED = ['Failed']
PAYPAL_RESPONSES_AUTHORIZED = []
PAYPAL_RESPONSES_DONE = ['Completed', 'Created', 'Refunded', 'Reversed', 'Processed']

@csrf.exempt
@paypalgateway.route('/ipn', methods=['POST'], endpoint="ipn")
@tryton.transaction()
def paypal_ipn(lang):
    """Signal Paypal confirmation payment

    protection_eligibility, last_name, txn_id, receiver_email, payment_status,
    payment_gross, tax, residence_country, address_state, payer_status, txn_type,
    address_country, handling_amount, payment_date, first_name, item_name,
    address_street, charset, custom, notify_version, address_name, test_ipn,
    item_numbe, receiver_id, transaction_subject, business, payer_id, verify_sign,
    address_zip, payment_fee, address_country_code, address_city, address_status,
    mc_fee, mc_currency, shipping, payer_email, payment_type, mc_gross,
    ipn_track_id, quantity
    """
    shop = Shop(SHOP)

    gateway = None
    for payment in shop.esale_payments:
        if payment.payment_type.gateway:
            payment_gateway = payment.payment_type.gateway
            if payment_gateway.method == 'paypal':
                gateway = payment_gateway
                break

    reference = request.form.get('item_name')
    response = request.form.get('payment_status')
    amount = Decimal(request.form.get('mc_gross'))
    authorisation_code = request.form.get('verify_sign')

    logs = []
    for k, v in request.form.iteritems():
        logs.append('%s: %s' % (k, v))
    log = "\n".join(logs)

    # Search transaction
    gtransactions = GatewayTransaction.search([
        ('reference_gateway', '=', reference),
        ('state', '=', 'draft'),
        ], limit=1)
    if gtransactions:
        gtransaction, = gtransactions
        gtransaction.authorisation_code = authorisation_code
        gtransaction.amount = amount
        gtransaction.log = log
        gtransaction.save()
    else:
        gtransaction = GatewayTransaction()
        gtransaction.description = reference
        gtransaction.authorisation_code = authorisation_code
        gtransaction.gateway = gateway
        gtransaction.reference_gateway = reference
        gtransaction.amount = amount
        gtransaction.log = log
        gtransaction.save()

    # Process transaction
    if response in PAYPAL_RESPONSES_CANCEL:
        GatewayTransaction.cancel([gtransaction])
        return response

    if response in PAYPAL_RESPONSES_PENDING:
        GatewayTransaction.pending([gtransaction])
        return response

    if response in PAYPAL_RESPONSES_FAILED:
        GatewayTransaction.cancel([gtransaction])
        return response

    if response in PAYPAL_RESPONSES_AUTHORIZED:
        GatewayTransaction.authorized([gtransaction])
        return response

    if response in PAYPAL_RESPONSES_DONE:
        GatewayTransaction.confirm([gtransaction])
        return response

    return 'ko'

@csrf.exempt
@paypalgateway.route('/confirm', methods=['GET', 'POST'], endpoint="confirm")
@tryton.transaction()
def paypal_confirm(lang):
    return render_template('paypal-confirm.html')

@csrf.exempt
@paypalgateway.route('/cancel', methods=['GET', 'POST'], endpoint="cancel")
@tryton.transaction()
def paypal_cancel(lang):
    return render_template('paypal-cancel.html')

@paypalgateway.route('/', methods=['POST'], endpoint="paypal")
@tryton.transaction()
def paypal_form(lang):
    shop = Shop(SHOP)

    base_url = current_app.config['BASE_URL']

    gateway = None
    for payment in shop.esale_payments:
        if payment.payment_type.gateway:
            payment_gateway = payment.payment_type.gateway
            if payment_gateway.method == 'paypal':
                gateway = payment_gateway
                break

    if not gateway:
        abort(404)

    url_ipn = '%s%s' % (base_url, url_for('.ipn', lang=g.language))
    url_confirm = '%s%s' % (base_url, url_for('.confirm', lang=g.language))
    url_cancel = '%s%s' % (base_url, url_for('.cancel', lang=g.language))

    origin = request.form.get('origin')
    if not origin:
        abort(404)
    try:
        o = origin.split(',')
        r = tryton.pool.get(o[0])(o[1])
    except:
        abort(500)
    reference = request.form.get('reference')
    if getattr(r, 'total_amount'):
        total_amount = getattr(r, 'total_amount')
    else:
        flash(_("Error when get total amount to pay. Repeat or contact us."),
            "danger")
        redirect(url_for('/', lang=g.language))
    amount = total_amount - r.gateway_amount

    currency = None
    if getattr(r, 'currency'):
        currency = getattr(r, 'currency')

    # save transaction draft
    gtransaction = GatewayTransaction()
    gtransaction.description = reference
    gtransaction.origin = origin
    gtransaction.gateway = gateway
    gtransaction.reference_gateway = reference
    gtransaction.party = session.get('customer', None)
    gtransaction.amount = amount
    if currency:
        gtransaction.currency = currency
    gtransaction.save()

    paypal_form = {}
    paypal_form['url'] = PAYPAL_SANDBOX_URL if current_app.config['DEBUG'] else PAYPAL_URL
    paypal_form['business'] = gateway.paypal_email
    paypal_form['reference'] = reference
    paypal_form['amount'] = amount
    paypal_form['currency'] = currency.code if currency else 'EUR' # TODO currency from company
    paypal_form['return'] = url_confirm
    paypal_form['cancel'] = url_cancel
    paypal_form['notify'] = url_ipn

    session['paypal_reference'] = reference

    return render_template('paypal.html',
            paypal_form=paypal_form,
            )
