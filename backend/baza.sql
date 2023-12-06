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
  `idKnives` INT NOT NULL,
  `knife_name` VARCHAR(100) NOT NULL,
  `min_price_with_fee` FLOAT NOT NULL,
  `min_price_without_fee` FLOAT NOT NULL,
  `buy_order_price` FLOAT NOT NULL,
  `last_updated` DATETIME NOT NULL,
  PRIMARY KEY (`idKnives`),
  UNIQUE INDEX `idKnives_UNIQUE` (`idKnives` ASC) VISIBLE,
  UNIQUE INDEX `Knife_name_UNIQUE` (`knife_name` ASC) VISIBLE)
ENGINE = InnoDB;


SET SQL_MODE=@OLD_SQL_MODE;
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;
