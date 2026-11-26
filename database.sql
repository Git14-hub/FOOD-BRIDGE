-- FoodBridge Database Schema (Single Source of Truth)
-- Re-runnable: uses IF NOT EXISTS and INSERT IGNORE.

CREATE DATABASE IF NOT EXISTS foodbridge CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE foodbridge;

CREATE TABLE IF NOT EXISTS districts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE,
    created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id INT AUTO_INCREMENT PRIMARY KEY,
    district_id INT NOT NULL,
    name VARCHAR(120) NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE KEY uq_district_area (district_id, name),
    CONSTRAINT fk_areas_district FOREIGN KEY (district_id) REFERENCES districts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(190) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL,
    phone VARCHAR(30) NOT NULL,
    organization_name VARCHAR(160) DEFAULT '',
    registration_number VARCHAR(100) DEFAULT '',
    address VARCHAR(255) NOT NULL,
    district_id INT NULL,
    area_id INT NULL,
    ngo_status VARCHAR(20) NOT NULL DEFAULT 'not_applicable',
    rejection_reason VARCHAR(255) DEFAULT '',
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_users_district FOREIGN KEY (district_id) REFERENCES districts(id) ON DELETE SET NULL,
    CONSTRAINT fk_users_area FOREIGN KEY (area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS donations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    donor_id INT NOT NULL,
    title VARCHAR(160) NOT NULL,
    category VARCHAR(80) NOT NULL,
    description TEXT,
    total_quantity DECIMAL(10, 2) NOT NULL,
    available_quantity DECIMAL(10, 2) NOT NULL,
    unit VARCHAR(30) NOT NULL,
    pickup_address VARCHAR(255) NOT NULL,
    district_id INT NOT NULL,
    area_id INT NOT NULL,
    prepared_at DATETIME NULL,
    expires_at DATETIME NOT NULL,
    image_path VARCHAR(255) DEFAULT '',
    status VARCHAR(30) NOT NULL DEFAULT 'available',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_donations_donor FOREIGN KEY (donor_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_donations_district FOREIGN KEY (district_id) REFERENCES districts(id),
    CONSTRAINT fk_donations_area FOREIGN KEY (area_id) REFERENCES areas(id)
);

CREATE TABLE IF NOT EXISTS food_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    donation_id INT NOT NULL,
    ngo_id INT NOT NULL,
    requested_quantity DECIMAL(10, 2) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    ngo_note VARCHAR(255) DEFAULT '',
    donor_note VARCHAR(255) DEFAULT '',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_requests_donation FOREIGN KEY (donation_id) REFERENCES donations(id) ON DELETE CASCADE,
    CONSTRAINT fk_requests_ngo FOREIGN KEY (ngo_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS food_collections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    donation_id INT NOT NULL,
    request_id INT NULL,
    actor_id INT NULL,
    from_status VARCHAR(30) DEFAULT '',
    to_status VARCHAR(30) NOT NULL,
    quantity DECIMAL(10, 2) DEFAULT 0,
    note VARCHAR(255) DEFAULT '',
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_collections_donation FOREIGN KEY (donation_id) REFERENCES donations(id) ON DELETE CASCADE,
    CONSTRAINT fk_collections_request FOREIGN KEY (request_id) REFERENCES food_requests(id) ON DELETE SET NULL,
    CONSTRAINT fk_collections_actor FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    donation_id INT NULL,
    request_id INT NULL,
    title VARCHAR(160) NOT NULL,
    message VARCHAR(500) NOT NULL,
    link VARCHAR(255) DEFAULT '',
    is_read TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Seed starter districts and areas (idempotent)
INSERT IGNORE INTO districts (id, name, created_at) VALUES
    (1, 'Central District', '2026-01-01 00:00:00'),
    (2, 'North District', '2026-01-01 00:00:00'),
    (3, 'South District', '2026-01-01 00:00:00');

INSERT IGNORE INTO areas (id, district_id, name, created_at) VALUES
    (1, 1, 'Downtown', '2026-01-01 00:00:00'),
    (2, 1, 'Market Square', '2026-01-01 00:00:00'),
    (3, 1, 'Civic Center', '2026-01-01 00:00:00'),
    (4, 2, 'Greenwood', '2026-01-01 00:00:00'),
    (5, 2, 'Hilltop', '2026-01-01 00:00:00'),
    (6, 3, 'Riverside', '2026-01-01 00:00:00'),
    (7, 3, 'Harbor View', '2026-01-01 00:00:00');
