package database

import (
	"context"
	"embed"
	"fmt"
	"sort"
	"strings"

	"gorm.io/gorm"
)

//go:embed migrations/*.sql
var migrations embed.FS

func RunMigrations(ctx context.Context, db *gorm.DB) error {
	if err := db.WithContext(ctx).Exec(`
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version varchar(128) PRIMARY KEY,
			applied_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
		)
	`).Error; err != nil {
		return err
	}

	entries, err := migrations.ReadDir("migrations")
	if err != nil {
		return err
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".sql") {
			names = append(names, entry.Name())
		}
	}
	sort.Strings(names)

	for _, name := range names {
		var count int64
		if err := db.WithContext(ctx).Raw(
			"SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
			name,
		).Scan(&count).Error; err != nil {
			return err
		}
		if count > 0 {
			continue
		}
		payload, err := migrations.ReadFile("migrations/" + name)
		if err != nil {
			return err
		}
		statements := splitSQL(string(payload))
		err = db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
			for _, statement := range statements {
				if err := tx.Exec(statement).Error; err != nil {
					return fmt.Errorf("%s: %w", name, err)
				}
			}
			return tx.Exec("INSERT INTO schema_migrations (version) VALUES (?)", name).Error
		})
		if err != nil {
			return err
		}
	}
	return nil
}

func splitSQL(payload string) []string {
	parts := strings.Split(payload, ";")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		trimmed := strings.TrimSpace(part)
		if trimmed != "" && !strings.HasPrefix(trimmed, "--") {
			out = append(out, trimmed)
		}
	}
	return out
}
