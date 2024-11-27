-- MySQL Workbench Forward Engineering

SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0;
SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0;
SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

-- -----------------------------------------------------
-- Schema knives
-- -----------------------------------------------------

-- -----------------------------------------------------
-- Schema knives
-- -----------------------------------------------------
CREATE SCHEMA IF NOT EXISTS `knives` DEFAULT CHARACTER SET utf8 ;
USE `knives` ;

-- -----------------------------------------------------
-- Table `knives`.`Knives`
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS `knives`.`Knives` (
  `knife_id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `knife_name` VARCHAR(150) NOT NULL,
  `current_min_price_with_fee` DECIMAL UNSIGNED NULL,
  `current_min_price_without_fee` DECIMAL UNSIGNED NULL,
  `last_min_price_with_fee` DECIMAL UNSIGNED NULL,
  `last_min_price_without_fee` DECIMAL UNSIGNED NULL,
  `buy_order_price` DECIMAL UNSIGNED NULL,
  `profit` DECIMAL GENERATED ALWAYS AS (last_min_price_without_fee - buy_order_price) STORED,
  `last_updated` DATETIME NULL,
  `last_sold` DATETIME NULL,
  `amount_sold` INT UNSIGNED NOT NULL DEFAULT 0,
  `selling_frequency` DECIMAL(10,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (`knife_id`),
  UNIQUE INDEX `idKnives_UNIQUE` (`knife_id` ASC) VISIBLE,
  UNIQUE INDEX `min_price_with_fee_copy1_UNIQUE` (`knife_name` ASC) VISIBLE)
ENGINE = InnoDB;


-- -----------------------------------------------------
-- Table `knives`.`SellTimes`
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS `knives`.`SellTimes` (
  `sell_time_id` INT NOT NULL AUTO_INCREMENT,
  `sell_time` DATETIME NOT NULL,
  PRIMARY KEY (`sell_time_id`),
  UNIQUE INDEX `sale_time_id_UNIQUE` (`sell_time_id` ASC) VISIBLE,
  UNIQUE INDEX `sale_time_UNIQUE` (`sell_time` ASC) VISIBLE)
ENGINE = InnoDB;


-- -----------------------------------------------------
-- Table `knives`.`SellHistory`
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS `knives`.`SellHistory` (
  `sell_history_id` INT NOT NULL AUTO_INCREMENT,
  `knife_id` INT UNSIGNED NOT NULL,
  `sell_time_id` INT NOT NULL,
  `price` DECIMAL NOT NULL,
  `quantity` INT NOT NULL,
  PRIMARY KEY (`sell_history_id`, `knife_id`, `sell_time_id`),
  UNIQUE INDEX `sell_history_id_UNIQUE` (`sell_history_id` ASC) VISIBLE,
  INDEX `fk_SellHistory_Knives_idx` (`knife_id` ASC) VISIBLE,
  INDEX `fk_SellHistory_SellTimes1_idx` (`sell_time_id` ASC) VISIBLE,
  CONSTRAINT `fk_SellHistory_Knives`
    FOREIGN KEY (`knife_id`)
    REFERENCES `knives`.`Knives` (`knife_id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION,
  CONSTRAINT `fk_SellHistory_SellTimes1`
    FOREIGN KEY (`sell_time_id`)
    REFERENCES `knives`.`SellTimes` (`sell_time_id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION)
ENGINE = InnoDB;


SET SQL_MODE=@OLD_SQL_MODE;
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;
