#!/usr/bin/python
#-*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import requests
import logging
_logger = logging.getLogger(__name__)

HOURS_PER_DAY = 8

class Company(models.Model):
	_inherit = "res.company"

	token_telegram = fields.Char(string='Token')

class Holidays(models.Model):
	_inherit = "hr.holidays"

	@api.model
	def telbot_sendtext(self, bot_message, bot_chatID):
		bot_token = self.env.user.company_id.token_telegram
		# bot_token = '1735183941:AAElkbQg2BOr6tKjyOal4HUxbvSepesMOQU'
		sent_text = 'https://api.telegram.org/bot' + bot_token + '/sendMessage?chat_id=' + bot_chatID + '&text=' + bot_message
		_logger.exception("URL : "+sent_text)
		response = requests.get(sent_text, timeout=10, verify=False)
		_logger.exception("Response "+str(response.json()))

	@api.model
	def create(self, values):
		employee_id = values.get('employee_id', False)
		if not self._check_state_access_right(values):
			raise AccessError(
				_('You cannot set a leave request as \'%s\'. Contact a human resource manager.') % values.get('state'))
		if not values.get('department_id'):
			values.update({'department_id': self.env['hr.employee'].browse(employee_id).department_id.id})
		# department_id = values.get('department_id', False)
		# manager_id = self.env['hr.department'].search([('user_id', '=', self.env.uid)], limit=1)
		holiday = super(Holidays, self.with_context(
			mail_create_nolog=True, mail_create_nosubscribe=True)).create(values)
		holiday.add_follower(employee_id)
		manager_id = holiday.department_id.manager_id
		if manager_id and manager_id.user_id.tele_id:
			tele_id = manager_id.user_id.tele_id
			name = manager_id.name
			user = holiday.employee_id.name
			message = "Hallo %s, Anda mendapatkan permintakan ijin/cuti/pulang awal dari %s. Periksa akun Odoo anda ya... \n https://aston.id/web/login." % (
				name,user)
			self.telbot_sendtext(message, tele_id)
		name = holiday.employee_id.name
		tele_id = holiday.employee_id.user_id.tele_id
		if tele_id:
			message = "Hallo %s, permohonan ijin/cuti/pulang awal kamu sedang diajukan. Mohon menunggu ya..." % (
                    name)
		self.telbot_sendtext(message, tele_id)
		return holiday

	@api.multi
	def action_approve(self):
		# if double_validation: this method is the first approval approval
		# if not double_validation: this method calls action_validate() below
		if not self.env.user.has_group('hr_holidays.group_hr_holidays_user'):
			raise UserError(_('Only an HR Officer or Manager can approve leave requests.'))

		manager = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
		for holiday in self:
			if holiday.state != 'confirm':
				raise UserError(_('Leave request must be confirmed ("To Approve") in order to approve it.'))

			if holiday.double_validation:
				manager_hr_all = self.env['res.users'].search([])
				for manager_hr in manager_hr_all:
					if manager_hr.has_group('hr_holidays.group_hr_holidays_user') and manager_hr.tele_id:
						tele_id = manager_hr.tele_id
						name = manager_hr.name
						user = holiday.employee_id.name
						message = "Hallo %s, Anda mendapatkan permintakan ijin/cuti/pulang awal dari %s. Periksa akun Odoo anda ya... \nhttps://aston.id/web/login." % (name, user)
						self.telbot_sendtext(message, tele_id)

				return holiday.write({'state': 'validate1', 'manager_id': manager.id if manager else False})
			else:
				holiday.action_validate()

	@api.multi
	def action_validate(self):
		if not self.env.user.has_group('hr_holidays.group_hr_holidays_user'):
			raise UserError(_('Only an HR Officer or Manager can approve leave requests.'))

		manager = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
		for holiday in self:
			if holiday.state not in ['confirm', 'validate1']:
				raise UserError(_('Leave request must be confirmed in order to approve it.'))
			if holiday.state == 'validate1' and not holiday.env.user.has_group('hr_holidays.group_hr_holidays_manager'):
				raise UserError(_('Only an HR Manager can apply the second approval on leave requests.'))

			holiday.write({'state': 'validate'})
			if holiday.double_validation:
				holiday.write({'manager_id2': manager.id})
			else:
				holiday.write({'manager_id': manager.id})

			name = holiday.employee_id.name
			tele_id = holiday.employee_id.user_id.tele_id
			if tele_id:
				message = "Hallo %s, permohonan ijin/cuti/pulang awal kamu disetujui. Periksa akun Odoo anda ya... \nhttps://aston.id/web/login." % (name)
				self.telbot_sendtext(message, tele_id)

			if holiday.holiday_type == 'employee' and holiday.type == 'remove':
				meeting_values = {
					'name': holiday.display_name,
					'categ_ids': [(6, 0, [holiday.holiday_status_id.categ_id.id])] if holiday.holiday_status_id.categ_id else [],
					'duration': holiday.number_of_days_temp * HOURS_PER_DAY,
					'description': holiday.notes,
					'user_id': holiday.user_id.id,
					'start': holiday.date_from,
					'stop': holiday.date_to,
					'allday': False,
					'state': 'open',            # to block that meeting date in the calendar
					'privacy': 'confidential'
				}
				#Add the partner_id (if exist) as an attendee
				if holiday.user_id and holiday.user_id.partner_id:
					meeting_values['partner_ids'] = [(4, holiday.user_id.partner_id.id)]

				meeting = self.env['calendar.event'].with_context(no_mail_to_attendees=True).create(meeting_values)
				holiday._create_resource_leave()
				holiday.write({'meeting_id': meeting.id})
			elif holiday.holiday_type == 'category':
				leaves = self.env['hr.holidays']
				for employee in holiday.category_id.employee_ids:
					values = holiday._prepare_create_by_category(employee)
					leaves += self.with_context(mail_notify_force_send=False).create(values)
				# TODO is it necessary to interleave the calls?
				leaves.action_approve()
				if leaves and leaves[0].double_validation:
					leaves.action_validate()
		return True

	@api.multi
	def action_refuse(self):
		if not self.env.user.has_group('hr_holidays.group_hr_holidays_user'):
			raise UserError(_('Only an HR Officer or Manager can refuse leave requests.'))

		manager = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
		for holiday in self:
			if holiday.state not in ['confirm', 'validate', 'validate1']:
				raise UserError(_('Leave request must be confirmed or validated in order to refuse it.'))

			if holiday.state == 'validate1':
				holiday.write({'state': 'refuse', 'manager_id': manager.id})
			else:
				holiday.write({'state': 'refuse', 'manager_id2': manager.id})
			# Delete the meeting
			if holiday.meeting_id:
				holiday.meeting_id.unlink()
			# If a category that created several holidays, cancel all related
			holiday.linked_request_ids.action_refuse()

			name = holiday.employee_id.name
			tele_id = holiday.employee_id.user_id.tele_id
			if tele_id:
				message = "Mohon maaf %s, permohonan ijin/cuti/pulang awal kamu ditolak. Periksa akun Odoo anda ya... \nhttps://aston.id/web/login." % (
					name)
				self.telbot_sendtext(message, tele_id)

		self._remove_resource_leave()
		return True

