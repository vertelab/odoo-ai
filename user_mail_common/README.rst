.. image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
    :alt: License

Administer mail for Odoo-users
==============================

* Updates a postfix / dovecot server
* Syncronize password updates
* Print instructions for mail

Mailaddress:
::
  +----------------------+-----------------------------------+------------------------+--------------------------------------------+
  |                      | mailaddress                       |  partner.email         |  messageing alias (when mail is installed) |
  +======================+===================================+========================+============================================+
  | login a login name   |<login name>@<domain>              |<login name>@<domain>   |  <login name>@<domain>                     |
  +----------------------+-----------------------------------+------------------------+--------------------------------------------+
  | login an external mail |<left part of login name>@<domain>|<login>                |  <left part of login name>@<domain>        |
  +----------------------+-----------------------------------+------------------------+--------------------------------------------+
  | login a local mail   |<login>                            |<login>                 |  <login>                                   |
  +----------------------+-----------------------------------+------------------------+--------------------------------------------+

Postfix and Dovecot are using user.postfix_mail as mailaddress
login?
maildir ?



Set allow_none=True in wsgi_server:

--- /usr/lib/python2.7/dist-packages/openerp/service/wsgi_server.py     2014-09-03 17:48:22.000000000 +0200
+++ wsgi_server.py      2015-11-04 11:30:54.382594999 +0100
@@ -73,7 +73,7 @@
     # exception handling.
     try:
         result = openerp.http.dispatch_rpc(service, method, params)
-        response = xmlrpclib.dumps((result,), methodresponse=1, allow_none=False, encoding=None)
+        response = xmlrpclib.dumps((result,), methodresponse=1, allow_none=True, encoding=None)
     except Exception, e:
         if string_faultcode:
             response = xmlrpc_handle_exception_string(e)


Credits
=======

Contributors
------------

 * Anders Wallenquist <anders.wallenquist@vertel.se>
 * Fredrik Tunholm <fredrik.tunholm@vertel.se>
