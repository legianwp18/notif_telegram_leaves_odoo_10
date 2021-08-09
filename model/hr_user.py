#!/usr/bin/python
#-*- coding: utf-8 -*-

from odoo import models, fields, api, _

class User(models.Model):
	_inherit = "res.users"

	tele_id = fields.Char( string="Telegram ID")


