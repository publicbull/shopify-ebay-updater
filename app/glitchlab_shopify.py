import os
import shopify
import logging
import pystache
import requests
import json
from flask import session, request
from flask import current_app as app


"""Logging setup"""
logger = logging.getLogger('glitchlab_shopify.slirp.aaronbeekay')
logger.setLevel(logging.DEBUG)
# Create console handler to dump messages to console
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
# Create formatter and add it to the console handler - omit time because it's handled elsewhere
formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
# Add the handler to the logger
logger.addHandler(ch)

def merge(a, b, path=None, update=True):
	"""
	Merges b into a, recursing through nested levels to only update changed keys.
	
	http://stackoverflow.com/questions/7204805/python-dictionaries-of-dictionaries-merge"""
  
	if path is None: path = []
	for key in b:
		if key in a:
			if isinstance(a[key], dict) and isinstance(b[key], dict):
				merge(a[key], b[key], path + [str(key)])
			elif a[key] == b[key]:
				pass # same leaf value
			elif isinstance(a[key], list) and isinstance(b[key], list):
				for idx, val in enumerate(b[key]):
					a[key][idx] = merge(a[key][idx], b[key][idx], path + [str(key), str(idx)], update=update)
			elif update:
				a[key] = b[key]
			else:
				raise RuntimeError('Dictionary merge conflict at %s' % '.'.join(path + [str(key)]))
		else:
			a[key] = b[key]
	return a

def render_product_template(template, product):
	"""make the HTML description for a product. template is a file path"""
	with open(template) as f:
		t = f.read()
	
	fields = {	'item_name': 			product.title,
				'item_description': 	product.body_html
				}
	
	return pystache.render(t, fields)
	
def get_shopify_product(product_id):
	"""
	Fetch a single Shopify product by its ID, `product_id`.
	Also retrieve the item's metafields, and merge the dict of metafields into the product dict returned from the Shopify product endpoint.

	Returns a dict if successful. Raises AuthenticationError or ItemNotFoundError, or returns None, if not.
	"""

	# Hit the Product endpoint first
	#   TODO: Why the fuck am I doing this manually when I have the Shopify API right here?
	url = 'https://' + app.config['SHOPIFY_STORE_DOMAIN'] + '/admin/api/2019-04/products/' + product_id + '.json'
	logger.debug("Trying to GET the Shopify product {} by hitting {}".format(product_id, url))
	logger.debug("Using auth: {}:{}".format(app.config['SHOPIFY_API_KEY'],app.config['SHOPIFY_API_PW']))
	response = requests.get(
		url,
		auth=(app.config['SHOPIFY_API_KEY'],app.config['SHOPIFY_API_PW'])
	)
	try:
		p = response.json()
	except json.JSONDecodeError:
		logger.error('Shopify said something that is not JSON: ' + response.text)
		return 'Shopify said...' + response.text
		
	# TODO: see if the response is a valid product
	
	# Retrieve metafields and add them in
	p['metafields'] = get_metafields( product_id )
	
	return p

def set_shopify_attributes(product_id, attributes):
	"""Set Shopify product attributes from a dict."""
	raise NotImplementedError("sorry")

def get_ebay_offer_ids( product_sku ):
	"""Get the eBay offer ID (or offer IDs) for a given product SKU."""
	
	offers = get_ebay_offers( product_sku )
	
	offer_ids = []
	for offer in offers:
		offer_ids.append(offer['offerId'])
		
	return offer_ids
	
def get_ebay_offers( product_sku ):
	"""Get all of the eBay offers for a given product SKU."""
	try:
		auth_token = session['access_token']
	except KeyError as e:
		raise AuthenticationError("No access token provided")
		
	url = app.config['EBAY_INVENTORYOFFERS_URL'].format( product_sku )
	
	headers = {
		'Authorization': 'Bearer {}'.format( auth_token ),
		'Content-Language': 'en-US'
		}
	logger.debug('Trying to get offers for eBay SKU {}...'.format( product_sku ))
	response = requests.get( url, headers=headers )
	logger.debug('Raw reply from eBay: {}'.format(response.text))
	
	try:
		j = response.json()
	except json.JSONDecodeError as e:
		logger.warning('Weird reply from eBay: {}'.format(response.text))
		return('eBay weird reply') #TODO should raise exception here
		
	offers = []
	
	if response.status_code == 404:
		# eBay says, no offers for that
		return(offers) #TODO should raise exception here
		
	handle_ebay_errors(j)
	
	try:
		for offer in response.json()['offers']:
			offers.append(offer)
	except KeyError:
		logger.warning("Didn't find errors OR offers in eBay reply...")
		return ('eBay weird reply') #TODO should raise exception here
		
	return offers
		
def get_ebay_offer( offer_id ):
	"""
	Get an eBay offer by its offer ID. You can find the Offer ID by calling `get_ebay_offer_ids`
	for a given SKU.
	
	The Offer object sets listing-specific details for an item (like fulfillment policy, etc).
	Specifically, it looks like the Shopify eBay integration uses the `listing_description` field
	of the Offer object for the item's HTML description, and NOT the `description` field of the
	`inventoryItem` itself.
	"""
	
	try:
		auth_token = session['access_token']
	except KeyError as e:
		raise AuthenticationError("No access token provided")
		
	url = app.config['EBAY_INVENTORYOFFER_URL'].format( offer_id )
	
	headers = {
		'Authorization': 'Bearer {}'.format( auth_token ),
		'Content-Language': 'en-US'
		}
	logger.debug('Trying to get offer ID {}...'.format( offer_id ))
	response = requests.get( url, headers=headers )
	logger.debug('Raw reply from eBay: {}'.format(response.text))
	
	try:
		j = response.json()
	except json.JSONDecodeError as e:
		logger.warning('Weird reply from eBay: {}'.format(response.text))
		return('eBay weird reply') #TODO should raise exception here
		
	offers = []
	
	if response.status_code == 404:
		# eBay says, no offers for that
		return(offers) #TODO should raise exception here
		
	handle_ebay_errors(j)
	
	return( j )
	
def update_ebay_offer( offer_id, update_fields ):
	"""
	Update an eBay offer by its ID (`offer_id`). 
	
	`update_ebay_offer()` will fetch the existing eBay offer, then merge in the fields provided in `update_fields`.
	
	It will fail if the eBay offer does not exist yet (you can't use it to make a new offer.)
	"""
	try:
		auth_token = session['access_token']
	except KeyError as e:
		raise AuthenticationError("No access token provided")
		
	"""1. Fetch old offer to update"""
	try:
		old = get_ebay_offer( offer_id )
	except ItemNotFoundError:
		logger.info('set_ebay_attributes called for offer ID {}, but eBay says item not found'.format(offer_id))
		return None
	
	"""2. Merge in new fields"""
	try:
		new = merge( old, update_fields )
	except RuntimeError as e:
		from pprint import pprint
		logger.error('Failed to merge new attributes into eBay offer dict. merge() says: {}'.format(e))
		logger.error('Attributes attempting to merge in: {}'.format(pprint(update_fields)) )
		
	"""3. Call eBay's updateOffer with the merged offer"""
	url = app.config['EBAY_INVENTORYOFFER_URL'].format( offer_id )
	headers = {
		'Authorization': 'Bearer {}'.format( auth_token ),
		'Content-Language': 'en-US'
		}
	logger.debug('Trying to update eBay offer ID {}...'.format( offer_id ))
	response = requests.put( url, headers=headers, json=new )
	logger.debug('Raw reply from eBay: {}'.format(response.text))
	
	if response.status_code == 204:
		# 204 No Content, this is a good thing
		logger.debug('eBay returned 204 No Content, assume all went well')
		return {}
		
	try:
		j = response.json()
	except json.JSONDecodeError:
		j = None
		logger.warning('Got a weird reply from eBay: {}'.format(response.text))
		raise RuntimeError("eBay weird reply")
		
	handle_ebay_errors( j )
	
	return None
		
def handle_ebay_errors( ebay_reply ):
	"""
	eBay API replies sometimes include an 'errors' field with number-coded errors.
	
	This function checks for the existence of that 'errors' field and raises the appropriate Python errors
	for any that are found.
	
	If no errors are found, it returns `None`.
	"""
	
	if 'errors' in ebay_reply:
		for e in ebay_reply['errors']:
			if e['errorId'] == app.config['constants']['EBAY_ERROR_SKU_NOT_FOUND']:
				raise ItemNotFoundError(e['message'])
			elif (	e['errorId'] == app.config['constants']['EBAY_ERROR_INVALID_ACCESS_TOKEN'] 	\
				or 	e['errorId'] == app.config['constants']['EBAY_ERROR_MISSING_ACCESS_TOKEN'] 	\
				or 	e['errorId'] == app.config['constants']['EBAY_ERROR_ACCESS_DENIED']		    ):
				raise AuthenticationError(e['message'])
			else:
				logger.warning("Unexpected eBay error: {}".format( json.dumps(ebay_reply) ))
				raise RuntimeError( ebay_reply )
	
def set_ebay_attributes(product_sku, attributes):
	"""Set eBay inventory item attributes from a dict."""
	
	try:
		auth_token = session['access_token']
	except KeyError as e:
		raise AuthenticationError("No access token provided")
	
	# 1. Fetch the existing inventory item (eBay will overwrite all fields when we update, so merge locally)
	try:
		iOld = get_ebay_product( auth_token, product_sku )
	except ItemNotFoundError:
		logger.info('set_ebay_attributes called for SKU {sku}, but eBay says item not found'.format(product_sku))
		return None
	
	# 2. Merge new attributes with existing inventory item
	try:
		iNew = merge(iOld, attributes)
	except RuntimeError as e:
		from pprint import pprint
		logger.error('Failed to merge new attributes into eBay product dict. merge() says: {}'.format(e))
		logger.error('Attributes attempting to merge in: {}'.format(pprint(attributes)) )
	
	# 3. Call createOrReplaceInventoryItem
	url = app.config['EBAY_INVENTORYITEM_URL'].format( product_sku )
	headers = {
		'Authorization': 'Bearer {}'.format( auth_token ),
		'Content-Language': 'en-US'
		}
	logger.debug('Trying to update eBay SKU {}...'.format( product_sku ))
	response = requests.put( url, headers=headers, json=iNew )
	logger.debug('Raw reply from eBay: {}'.format(response.text))
	
	if response.status_code == 204:
		# 204 No Content, this is a good thing
		logger.debug('eBay returned 204 No Content, assume all went well')
		return {}
		
	try:
		j = response.json()
	except json.JSONDecodeError:
		j = None
		logger.warning('Got a weird reply from eBay: {}'.format(response.text))
		
	if 'errors' in j:
		for e in j['errors']:
			if e['errorId'] == app.config['constants']['EBAY_ERROR_SKU_NOT_FOUND']:
				raise ItemNotFoundError(e['message'])
			elif (	e['errorId'] == app.config['constants']['EBAY_ERROR_INVALID_ACCESS_TOKEN'] 	\
				or 	e['errorId'] == app.config['constants']['EBAY_ERROR_MISSING_ACCESS_TOKEN'] 	\
				or 	e['errorId'] == app.config['constants']['EBAY_ERROR_ACCESS_DENIED']		    ):
				raise AuthenticationError(e['message'])
			else:
				logger.warning("Unexpected eBay error: {}".format( response.text ))
				return(j, 406)
				
	return j
	
def get_ebay_product(auth_token, product_sku):
	"""
	Fetch the eBay product details for product with sku `product_sku`. Return them as a dict.
	
	Raises AuthenticationError if not authenticated.
	
	Raises ItemNotFoundError if item doesn't exist.
	"""
	url = app.config['EBAY_INVENTORYITEM_URL'].format(product_sku)
	auth = {'Authorization': 'Bearer {}'.format(auth_token)}
	logger.debug('Trying to fetch eBay SKU {}...'.format(product_sku))
	response = requests.get( url, headers=auth )
	
	try:
		j = response.json()
	except json.JSONDecodeError:
		logger.debug('Got a weird reply from eBay: {}'.format(response.text))
		
	handle_ebay_errors( j )
				
	return j
				
def shopify_authenticate(api_key=None, api_password=None):
	"""Authenticate with the Shopify API given a certain API key and password. If none given, check the app config""" 
	
	if api_key is None:
		try:
			api_key = app.config['SHOPIFY_API_KEY']
			logger.debug('Setting shopify API key from app.config: {}'.format(api_key))
		except KeyError:
			raise RuntimeError("Didn't find a Shopify API key in the SHOPIFY_API_KEY environment variable")
		
	if api_password is None:
		try:
			api_password = app.config['SHOPIFY_API_PW']
			logger.debug('Setting shopify API PW from app.config: {}'.format(api_password))
		except KeyError:
			raise RuntimeError("Didn't find a Shopify API password in the SHOPIFY_API_PW environment variable")

	shop_url = "https://{k}:{pw}@glitchlab.myshopify.com/admin".format(k=api_key, pw=api_password)
	logger.debug('Setting shop url: {}'.format(shop_url))
	shopify.ShopifyResource.set_site(shop_url)
	
# def get_metafields(id):
# 	"""Return a dict of metafields and values for a Shopify product"""
# 	shopify_authenticate()
# 	product = shopify.Product.find(id)
# 	
# 	d = {}
# 	#import pdb;pdb.set_trace()
# 	for m in product.metafields():
# 		k = m.key
# 		
# 		if m.value_type == 'integer':
# 			v = int(m.value)
# 		else:
# 			v = m.value
# 		
# 		d[k] = v
# 	return d

def get_metafields(product_id, with_ids=False):
	"""
	Get product metafields without using shopify api
	
	If `with_ids` is True, will return a dict of {key: {'id': id, 'value': value, 'value_type': value_type}}.
	If `with_ids` is False, will return a dict of {key: value}.
	
	value_type is one of ("string", "json_string", "integer"), per Shopify.
	"""
	
	#   TODO: Why the fuck am I doing this manually when I have the Shopify API right here?
	url = 'https://' + app.config['SHOPIFY_STORE_DOMAIN'] + '/admin/api/2019-04/products/' + product_id + '/metafields.json'
	logger.debug("Trying to GET the Shopify product metafields {} by hitting {}".format(product_id, url))
	logger.debug("Using auth: {}:{}".format(app.config['SHOPIFY_API_KEY'],app.config['SHOPIFY_API_PW']))
	response = requests.get(
		url,
		auth=(app.config['SHOPIFY_API_KEY'],app.config['SHOPIFY_API_PW'])
	)
	try:
		ms = response.json()
	except json.JSONDecodeError:
		logger.error('Shopify said something that is not JSON: ' + response.text)
		return 'Shopify said...' + response.text
	
	# Construct a more sensible array of metafields (key => value)
	m = {}
	if with_ids is False:
		for f in ms['metafields']:
			m[f['key']] = f['value']
	elif with_ids is True:
		for f in ms['metafields']:
			m[f['key']] = {'id': f['id'], 'value': f['value'], 'value_type': f['value_type']}		
	return m
	
def set_metafields(product, metafields):
	"""Given a dict of metafield keys and values, set a product metafield for each key. If a metafield exists
		with that metafield key, it will be updated with the value provided. If a metafield does not exist
		with that metafield key, it will be created. """
		
	existing = get_metafields(product)
		
	for k,v in metafields.items():
		mf = shopify.Metafield({	"key": k,
				"value": v,
				"value_type": "string",		# TODO do this right
				"namespace": "global"})
		product.add_metafield(mf)			# this will update the metafield if the key exists
			
		if product.save() is False:
			message = 'Couldn\'t save product {}. Error messages: {}...'.format(
					product['Handle'],
					product.errors.full_messages()	)
			logger.error(message)
			raise RuntimeError(message)
		else:
			logger.info('Successfully updated product id {} with metafield {} = {}'.format(product.id, k, v))
			
def set_metafield(product_id, key, value):
	"""
	Create or update a single metafield for a product with ID `product_id`.
	
	set_metafield() will download the existing metafields for the product. If the metafield with key `key` exists, 
		its value will be updated to be `value`. If not, it will be created.
	
	Doesn't return anything.
	"""

	logger.debug('Going to set metafield with key={} to {} for product {}.'.format(key, value, product_id))
	existing = get_metafields(product_id, with_ids=True)
	
	if key in existing:
		logger.debug('Looks like {k} already exists for product {pid} (currently set to {v}). Will update.'.format(k=key,pid=product_id,v=existing[key]['value']))
		# Update that particular metafield
		metafield_id = existing[key]['id']
		update_data = {"metafield": {"id": metafield_id, "key": key, "value": value}}
		
		url = 'https://' + app.config['SHOPIFY_STORE_DOMAIN'] + '/admin/api/2019-04/products/' + 	\
				product_id + '/metafields/' + metafield_id + '.json'
		response = requests.put(
			url,
			auth=(app.config['SHOPIFY_API_KEY'],app.config['SHOPIFY_API_PW']),
			json=update_data
			)
	else:
		# Make a new metafield
		type_string = guess_metafield_type(value)
		new_data = {"metafield": {"key": key, "value": value, "value_type": type_string, "namespace": "global"}}
		
		url = 'https://' + app.config['SHOPIFY_STORE_DOMAIN'] + '/admin/api/2019-04/products/' + 	\
				product_id + '/metafields.json'
		logger.debug('OK, making a new metafield for product {}: {}'.format(product_id, json.dumps(new_data)))
		response = requests.post(
			url,
			auth=(app.config['SHOPIFY_API_KEY'],app.config['SHOPIFY_API_PW']),
			json=new_data
			)

	if response.status_code >= 200 and response.status_code < 300: 
		logger.debug('Got status in 200s back from Shopify, assuming metafield set OK.')
		return	# assume all is well
	else:
		logger.warning('Got unexpected response from Shopify: {}'.format(response.text))
		return response.text
		

def guess_metafield_type(value):
	"""
	Guess the correct type_string to use with a given metafield value.
	
	Returns one of ('string', 'json_string', 'integer').
	"""
	
	# Pick one of ('string', 'json_string', 'integer') to set as the Shopify metafield type.
	if isinstance(value, (dict, list)):
		# If we have a collection input, dump it to a string and set the metafield to a JSON string.
		type_string = 'json_string'
	elif isinstance(value, str):
		# If it's a string, check if it's valid JSON. If so, set JSON string. Otherwise, regular string.
		try:
			json.loads(value)
			type_string = 'json_string'
		except json.JSONDecodeError:
			type_string = 'string'
	elif isinstance(value, int):
		type_string = 'integer'
	else:
		type_string = 'string'
	
	return type_string
			
"""Error classes"""
class Error(Exception):
	"""Base class for exceptions"""
	pass

class AuthenticationError(Error):
	"""Raised when an external service rejects our credentials for one reason or another"""
	
	def __init__(self, message):
		self.message = message
		
class ItemNotFoundError(Error):
	"""Raised when we try to look up an item and we don't find it."""

	def __init__(self, message):
		self.message = message		