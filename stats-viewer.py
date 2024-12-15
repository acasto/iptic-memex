#!/usr/bin/env python3
import os
import sqlite3
from datetime import timedelta
import configparser

# Configuration
DB_PATH = os.path.expanduser('~/.config/iptic-memex/db.sqlite')
MODELS_INI_PATH = 'models.ini'
USER_MODELS_INI_PATH = os.path.expanduser('~/.config/iptic-memex/models.ini')
# Local models and the models to compare pricing against
LOCAL_MODELS = ['llama-3.3', 'llama-3.1']
COMPARISON_MODELS = ['gpt-4o', 'sonnet-3.5']

# Load models configuration
config = configparser.ConfigParser()
config.read(MODELS_INI_PATH)
config.read(USER_MODELS_INI_PATH)


def format_time(seconds):
    """Convert seconds to human readable duration"""
    return str(timedelta(seconds=round(float(seconds))))


def format_number(n):
    """Format number with thousands separator"""
    return format(int(n), ',')


def get_stats(local_models, comparison_models):
    """Retrieve and format statistics from database"""
    if not os.path.exists(DB_PATH):
        print(f"Database not found at: {DB_PATH}")
        return

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key, value FROM keyvalue ORDER BY key')
            results = cursor.fetchall()

        # Group stats by prefix
        stats = {'total': {}, 'models': {}}
        stat_suffixes = ['_time', '_tokens_in', '_tokens_out']

        for key, value in results:
            if key.startswith('total_'):
                stats['total'][key] = value
            elif key.startswith('model_'):
                # Only process keys with known suffixes
                if not any(key.endswith(suffix) for suffix in stat_suffixes):
                    continue

                # Extract model name by removing 'model_' prefix and known suffix
                model_name = key[6:]  # remove 'model_'
                for suffix in stat_suffixes:
                    if model_name.endswith(suffix):
                        model_name = model_name[:-len(suffix)]
                        stat_type = suffix[1:]  # remove leading underscore
                        break

                if model_name not in stats['models']:
                    stats['models'][model_name] = {}
                stats['models'][model_name][stat_type] = value

        # Print overall totals
        if stats['total']:
            total_in = int(stats['total'].get('total_tokens_in', 0))
            total_out = int(stats['total'].get('total_tokens_out', 0))
            print("Overall Usage:")
            print(f"  Total Tokens In:  {format_number(total_in)}")
            print(f"  Total Tokens Out: {format_number(total_out)}")
            print(f"  Total Tokens:     {format_number(total_in + total_out)}")
            if 'total_time' in stats['total']:
                print(f"  Total Time:       {format_time(stats['total']['total_time'])}")
            print()

        # Print per-model stats
        if stats['models']:
            print("Per-Model Usage:")
            for model_name in sorted(stats['models'].keys()):
                model = stats['models'][model_name]
                tokens_in = int(model.get('tokens_in', 0))
                tokens_out = int(model.get('tokens_out', 0))
                print(f"  {model_name}:")
                print(f"    Tokens In:  {format_number(tokens_in)}")
                print(f"    Tokens Out: {format_number(tokens_out)}")
                print(f"    Total:      {format_number(tokens_in + tokens_out)}")
                if 'time' in model:
                    print(f"    Time:       {format_time(model['time'])}")
                print()

        # Calculate local model usage
        local_tokens_in = 0
        local_tokens_out = 0
        for model_name in local_models:
            if model_name in stats['models']:
                model = stats['models'][model_name]
                local_tokens_in += int(model.get('tokens_in', 0))
                local_tokens_out += int(model.get('tokens_out', 0))

        print(f"Local tokens in: {local_tokens_in}")
        print(f"Local tokens out: {local_tokens_out}")
        print()

        # Print pricing comparison
        print("Pricing Comparison:")
        default_price_unit = float(config['DEFAULT'].get('price_unit', 1000000))
        for comparison_model in comparison_models:
            if comparison_model not in config:
                print(f"  Warning: {comparison_model} not found in models.ini")
                continue

            comparison_model_config = config[comparison_model]
            price_in = float(comparison_model_config.get('price_in', 0))
            price_out = float(comparison_model_config.get('price_out', 0))
            price_unit = float(comparison_model_config.get('price_unit', default_price_unit))
            estimated_cost = ((local_tokens_in / price_unit) * price_in) + ((local_tokens_out / price_unit) * price_out)
            print(f"  Using {comparison_model}: ${estimated_cost:.2f}")
        print()


    except sqlite3.Error as e:
        print(f"Database error: {e}")


if __name__ == "__main__":
    get_stats(LOCAL_MODELS, COMPARISON_MODELS)
