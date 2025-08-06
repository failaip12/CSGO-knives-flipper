-- PostgreSQL version of the schema

-- -----------------------------------------------------
-- Table Knives
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS Knives (
  knife_id SERIAL PRIMARY KEY,
  knife_name VARCHAR(150) NOT NULL UNIQUE,
  current_min_price_with_fee DECIMAL,
  current_min_price_without_fee DECIMAL,
  last_min_price_with_fee DECIMAL,
  last_min_price_without_fee DECIMAL,
  buy_order_price DECIMAL,
  profit DECIMAL GENERATED ALWAYS AS (last_min_price_without_fee - buy_order_price) STORED,
  last_updated TIMESTAMP NULL,
  last_sold TIMESTAMP NULL,
  amount_sold INT NOT NULL DEFAULT 0,
  selling_frequency DECIMAL(10,2) NOT NULL DEFAULT 0,
  amount_sold_last_year INT NOT NULL DEFAULT 0,
  knife_image VARCHAR(400) NULL
);


-- -----------------------------------------------------
-- Table SellTimes
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS SellTimes (
  sell_time_id SERIAL PRIMARY KEY,
  sell_time TIMESTAMP NOT NULL UNIQUE
);


-- -----------------------------------------------------
-- Table SellHistory
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS SellHistory (
  sell_history_id SERIAL PRIMARY KEY,
  knife_id INT NOT NULL,
  sell_time_id INT NOT NULL,
  price DECIMAL NOT NULL,
  quantity INT NOT NULL,
  UNIQUE (knife_id, sell_time_id),
  CONSTRAINT fk_SellHistory_Knives
    FOREIGN KEY (knife_id)
    REFERENCES Knives (knife_id)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION,
  CONSTRAINT fk_SellHistory_SellTimes1
    FOREIGN KEY (sell_time_id)
    REFERENCES SellTimes (sell_time_id)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION
);
