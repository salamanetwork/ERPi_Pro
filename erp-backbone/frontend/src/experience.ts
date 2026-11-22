export const kindLabels: Record<string, string> = {
  helpdesk: 'Helpdesk', crm: 'CRM', hr: 'Human Resources', payroll: 'Payroll',
  si: "Supplier invoices",
  pr: 'Purchase requisitions', po: 'Purchase orders', grn: 'Goods receipts', wo: 'Work orders',
  sales: 'Sales', asset: 'Assets', journal: 'Journal entries', customer: 'Customers',
  supplier: 'Suppliers', item: 'Inventory items',
}

export const actionLabels: Record<string, string> = {
  marketing_complaint: 'Marketing complaint', marketing_timezone: 'Marketing campaign timezone changed',
  helpdesk_opened:'Ticket opened', helpdesk_assigned:'Ticket assigned', helpdesk_commented:'Comment added',
  helpdesk_sla_first_response:'First response overdue', helpdesk_sla_resolution:'Resolution overdue',
  helpdesk_resolved:'Ticket resolved', helpdesk_transition:'Status changed',
  crm_activity_due: 'CRM activity due', crm_customer_review: 'Review CRM customer request',
  review_attendance_correction: 'Review attendance correction',
  approve_timesheet: 'Approve timesheet',
  approve_payroll: 'Approve payroll', approve_leave: 'Approve leave',
  execute_maintenance: "Execute maintenance",
  close_maintenance: "Close work order",
  post_invoice: "Post supplier invoice",
  review: 'Review required', issue: 'Issue purchase order', confirm: 'Confirm goods receipt',
  confirm_order: 'Confirm order', invoice: 'Issue invoice', approve: 'Approved',
  return: 'Returned for changes', posting_pending: 'Posting needs attention',
}
