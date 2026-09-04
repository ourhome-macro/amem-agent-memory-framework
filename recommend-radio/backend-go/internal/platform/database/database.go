package database

import (
	"context"
	"database/sql"

	"gorm.io/driver/mysql"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func Open(dsn string) (*gorm.DB, *sql.DB, error) {
	db, err := gorm.Open(mysql.Open(dsn), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Warn),
	})
	if err != nil {
		return nil, nil, err
	}
	sqlDB, err := db.DB()
	if err != nil {
		return nil, nil, err
	}
	sqlDB.SetMaxOpenConns(32)
	sqlDB.SetMaxIdleConns(16)
	return db, sqlDB, nil
}

func Ping(ctx context.Context, sqlDB *sql.DB) error {
	return sqlDB.PingContext(ctx)
}
