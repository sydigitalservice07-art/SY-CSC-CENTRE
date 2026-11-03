-- ===========================================================================
-- SY CSC CENTRE  -  MySQL / MariaDB schema
-- ===========================================================================
-- Money is always DECIMAL(12,2).  Never FLOAT / DOUBLE.
-- Every business table carries created_at / created_by (and updated_* where
-- the record can be edited) so that every change is traceable.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  username VARCHAR(60) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('admin','operator') NOT NULL DEFAULT 'operator',
  mobile VARCHAR(20) NULL,
  email VARCHAR(120) NULL,
  address VARCHAR(255) NULL,
  status ENUM('active','inactive') NOT NULL DEFAULT 'active',
  last_login DATETIME NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NULL,
  created_by INT NULL,
  updated_by INT NULL,
  CONSTRAINT uq_users_username UNIQUE (username),
  CONSTRAINT fk_users_created_by FOREIGN KEY (created_by) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS operator_permissions (
  user_id INT NOT NULL PRIMARY KEY,
  can_add_customer TINYINT(1) NOT NULL DEFAULT 0,
  can_edit_customer TINYINT(1) NOT NULL DEFAULT 1,
  can_add_application TINYINT(1) NOT NULL DEFAULT 1,
  can_update_status TINYINT(1) NOT NULL DEFAULT 1,
  can_view_reports TINYINT(1) NOT NULL DEFAULT 0,
  can_reply_messages TINYINT(1) NOT NULL DEFAULT 1,
  can_access_archive TINYINT(1) NOT NULL DEFAULT 0,
  can_override_duplicate TINYINT(1) NOT NULL DEFAULT 0,
  updated_at DATETIME NULL,
  updated_by INT NULL,
  CONSTRAINT fk_operm_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS customers (
  id INT AUTO_INCREMENT PRIMARY KEY,
  customer_code VARCHAR(40) NOT NULL,
  surname VARCHAR(60) NOT NULL,
  first_name VARCHAR(60) NOT NULL,
  father_surname VARCHAR(60) NULL,
  father_name VARCHAR(60) NULL,
  dob DATE NULL,
  gender ENUM('Male','Female','Other') NULL,
  marital_status ENUM('Single','Married','Divorced','Widowed','Other') NULL,
  mobile VARCHAR(10) NOT NULL,
  alt_mobile VARCHAR(10) NULL,
  email VARCHAR(120) NULL,
  street VARCHAR(120) NULL,
  society VARCHAR(120) NULL,
  colony VARCHAR(120) NULL,
  house_no VARCHAR(40) NULL,
  village VARCHAR(80) NOT NULL,
  taluka VARCHAR(80) NOT NULL,
  post_office VARCHAR(80) NOT NULL,
  district VARCHAR(80) NOT NULL,
  state VARCHAR(80) NOT NULL,
  pincode VARCHAR(6) NOT NULL,
  mother_surname VARCHAR(60) NULL,
  mother_name VARCHAR(60) NULL,
  aadhaar_name VARCHAR(120) NULL,
  aadhaar_number VARCHAR(12) NULL,
  photo_path VARCHAR(255) NULL,
  signature_path VARCHAR(255) NULL,
  notes TEXT NULL,
  status ENUM('active','archived') NOT NULL DEFAULT 'active',
  archived_at DATETIME NULL,
  archived_by INT NULL,
  archive_reason VARCHAR(255) NULL,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  updated_at DATETIME NULL,
  updated_by INT NULL,
  CONSTRAINT uq_customer_code UNIQUE (customer_code),
  CONSTRAINT fk_customer_created_by FOREIGN KEY (created_by) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS customer_documents (
  id INT AUTO_INCREMENT PRIMARY KEY,
  customer_id INT NOT NULL,
  doc_name VARCHAR(120) NOT NULL,
  doc_type VARCHAR(60) NULL,
  file_path VARCHAR(255) NOT NULL,
  is_protected TINYINT(1) NOT NULL DEFAULT 0,
  password_hash VARCHAR(255) NULL,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  CONSTRAINT fk_cdoc_customer FOREIGN KEY (customer_id) REFERENCES customers (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS services (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  category VARCHAR(80) NULL,
  description TEXT NULL,
  form_key ENUM('pan','ration','ayushman','abha','voting','generic') NOT NULL DEFAULT 'generic',
  default_charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  default_govt_charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  default_csc_charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  processing_days INT NULL,
  required_documents VARCHAR(255) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  updated_at DATETIME NULL,
  updated_by INT NULL,
  CONSTRAINT uq_service_name UNIQUE (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sub_services (
  id INT AUTO_INCREMENT PRIMARY KEY,
  service_id INT NOT NULL,
  name VARCHAR(120) NOT NULL,
  default_charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  default_govt_charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  default_csc_charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  updated_at DATETIME NULL,
  updated_by INT NULL,
  CONSTRAINT uq_subservice UNIQUE (service_id, name),
  CONSTRAINT fk_sub_service FOREIGN KEY (service_id) REFERENCES services (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS applications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  app_code VARCHAR(40) NOT NULL,
  customer_id INT NOT NULL,
  service_id INT NOT NULL,
  sub_service_id INT NULL,
  operator_id INT NULL,
  apply_date DATE NOT NULL,
  status ENUM('Draft','Pending','In Progress','Submitted','Completed','Rejected','Cancelled') NOT NULL DEFAULT 'Pending',
  charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  govt_charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  csc_charge DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  total DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  charge_paid DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  govt_paid DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  csc_paid DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  amount_paid DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  amount_pending DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  notes TEXT NULL,
  is_archived TINYINT(1) NOT NULL DEFAULT 0,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  delete_reason VARCHAR(255) NULL,
  deleted_at DATETIME NULL,
  deleted_by INT NULL,
  completed_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  updated_at DATETIME NULL,
  updated_by INT NULL,
  CONSTRAINT uq_app_code UNIQUE (app_code),
  CONSTRAINT fk_app_customer FOREIGN KEY (customer_id) REFERENCES customers (id),
  CONSTRAINT fk_app_service FOREIGN KEY (service_id) REFERENCES services (id),
  CONSTRAINT fk_app_subservice FOREIGN KEY (sub_service_id) REFERENCES sub_services (id),
  CONSTRAINT fk_app_operator FOREIGN KEY (operator_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Service specific fields (the five PDF forms share this one structured table;
-- only the columns that belong to the selected service are filled).
CREATE TABLE IF NOT EXISTS application_details (
  id INT AUTO_INCREMENT PRIMARY KEY,
  application_id INT NOT NULL,
  token_no VARCHAR(60) NULL,
  ack_no VARCHAR(60) NULL,
  epan_date DATE NULL,
  postal_tracking_no VARCHAR(80) NULL,
  physical_pan_date DATE NULL,
  ration_card_no VARCHAR(60) NULL,
  ap_number VARCHAR(60) NULL,
  pay_number VARCHAR(60) NULL,
  done_date DATE NULL,
  document_name VARCHAR(160) NULL,
  total_days INT NULL,
  extra_json TEXT NULL,
  CONSTRAINT uq_appdetail_app UNIQUE (application_id),
  CONSTRAINT fk_appdetail_app FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS application_archive (
  id INT AUTO_INCREMENT PRIMARY KEY,
  application_id INT NOT NULL,
  archived_at DATETIME NOT NULL,
  archived_by INT NULL,
  reason VARCHAR(255) NULL,
  CONSTRAINT uq_archive_app UNIQUE (application_id),
  CONSTRAINT fk_archive_app FOREIGN KEY (application_id) REFERENCES applications (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS payments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  payment_code VARCHAR(60) NOT NULL,
  customer_id INT NOT NULL,
  application_id INT NOT NULL,
  payment_date DATETIME NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  charge_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  govt_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  csc_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  payment_mode ENUM('Cash','Bank','UPI') NOT NULL DEFAULT 'Cash',
  reference_no VARCHAR(80) NULL,
  notes VARCHAR(255) NULL,
  is_refund TINYINT(1) NOT NULL DEFAULT 0,
  status ENUM('active','reversed') NOT NULL DEFAULT 'active',
  reversed_at DATETIME NULL,
  reversed_by INT NULL,
  reversal_reason VARCHAR(255) NULL,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  CONSTRAINT uq_payment_code UNIQUE (payment_code),
  CONSTRAINT fk_pay_customer FOREIGN KEY (customer_id) REFERENCES customers (id),
  CONSTRAINT fk_pay_app FOREIGN KEY (application_id) REFERENCES applications (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ledger_entries (
  id INT AUTO_INCREMENT PRIMARY KEY,
  voucher_no VARCHAR(40) NOT NULL,
  entry_date DATE NOT NULL,
  account ENUM('Cash','Bank','UPI') NOT NULL,
  entry_type ENUM('credit','debit') NOT NULL,
  category VARCHAR(60) NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  description VARCHAR(255) NULL,
  reference_no VARCHAR(80) NULL,
  payment_id INT NULL,
  application_id INT NULL,
  customer_id INT NULL,
  transfer_group VARCHAR(40) NULL,
  is_reversed TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  CONSTRAINT fk_ledger_payment FOREIGN KEY (payment_id) REFERENCES payments (id),
  CONSTRAINT fk_ledger_app FOREIGN KEY (application_id) REFERENCES applications (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS account_balances (
  account ENUM('Cash','Bank','UPI') NOT NULL PRIMARY KEY,
  opening_balance DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  updated_at DATETIME NULL,
  updated_by INT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sender_id INT NOT NULL,
  recipient_id INT NOT NULL,
  parent_id INT NULL,
  subject VARCHAR(160) NOT NULL,
  body TEXT NOT NULL,
  is_broadcast TINYINT(1) NOT NULL DEFAULT 0,
  read_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT fk_msg_sender FOREIGN KEY (sender_id) REFERENCES users (id),
  CONSTRAINT fk_msg_recipient FOREIGN KEY (recipient_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS attendance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  operator_id INT NOT NULL,
  att_date DATE NOT NULL,
  check_in TIME NULL,
  check_out TIME NULL,
  notes VARCHAR(255) NULL,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  updated_at DATETIME NULL,
  CONSTRAINT uq_attendance UNIQUE (operator_id, att_date),
  CONSTRAINT fk_att_operator FOREIGN KEY (operator_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS operator_salaries (
  id INT AUTO_INCREMENT PRIMARY KEY,
  operator_id INT NOT NULL,
  salary_month VARCHAR(7) NOT NULL,
  salary_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  paid_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  payment_date DATE NULL,
  notes VARCHAR(255) NULL,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  updated_at DATETIME NULL,
  updated_by INT NULL,
  CONSTRAINT uq_salary_month UNIQUE (operator_id, salary_month),
  CONSTRAINT fk_sal_operator FOREIGN KEY (operator_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS salary_payments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  salary_id INT NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  paid_on DATE NOT NULL,
  payment_mode ENUM('Cash','Bank','UPI') NOT NULL DEFAULT 'Cash',
  notes VARCHAR(255) NULL,
  created_at DATETIME NOT NULL,
  created_by INT NULL,
  CONSTRAINT fk_salpay_salary FOREIGN KEY (salary_id) REFERENCES operator_salaries (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS leaves (
  id INT AUTO_INCREMENT PRIMARY KEY,
  operator_id INT NOT NULL,
  from_date DATE NOT NULL,
  to_date DATE NOT NULL,
  reason VARCHAR(255) NOT NULL,
  request_date DATE NOT NULL,
  status ENUM('Pending','Approved','Rejected') NOT NULL DEFAULT 'Pending',
  admin_remarks VARCHAR(255) NULL,
  approved_by INT NULL,
  approved_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  CONSTRAINT fk_leave_operator FOREIGN KEY (operator_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  username VARCHAR(60) NULL,
  action VARCHAR(60) NOT NULL,
  entity VARCHAR(60) NULL,
  entity_id VARCHAR(60) NULL,
  details TEXT NULL,
  ip_address VARCHAR(45) NULL,
  created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS settings (
  setting_key VARCHAR(60) NOT NULL PRIMARY KEY,
  setting_value TEXT NULL,
  updated_at DATETIME NULL,
  updated_by INT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS id_sequences (
  id INT AUTO_INCREMENT PRIMARY KEY,
  scope VARCHAR(20) NOT NULL,
  key_part VARCHAR(40) NOT NULL,
  next_val INT NOT NULL DEFAULT 1,
  CONSTRAINT uq_sequence UNIQUE (scope, key_part)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS backup_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  action ENUM('backup','restore','reset') NOT NULL,
  file_name VARCHAR(255) NULL,
  record_count INT NULL,
  status VARCHAR(20) NOT NULL,
  message VARCHAR(255) NULL,
  created_at DATETIME NOT NULL,
  created_by INT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
