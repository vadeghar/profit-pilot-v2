"""Contract specifications the backtest engine needs to value a position: MCX commodity futures
lot sizes and rupee value per point (index lot sizes live in platform_config/universe.yaml)."""

COMMODITY_SPECS = {
    'MCX_CRUDEOIL': {'lot_size': 100, 'point_value': 100, 'tick_size': 1.0, 'name': 'Crude Oil'},
    'CRUDEOIL': {'lot_size': 100, 'point_value': 100, 'tick_size': 1.0, 'name': 'Crude Oil'},
    'MCX:CRUDEOIL': {'lot_size': 100, 'point_value': 100, 'tick_size': 1.0, 'name': 'Crude Oil'},
    'MCX_GOLD': {'lot_size': 1, 'point_value': 1, 'tick_size': 1.0, 'name': 'Gold 1kg'},
    'GOLD': {'lot_size': 1, 'point_value': 1, 'tick_size': 1.0, 'name': 'Gold 1kg'},
    'MCX:GOLD': {'lot_size': 1, 'point_value': 1, 'tick_size': 1.0, 'name': 'Gold 1kg'},
    'MCX_GOLDM': {'lot_size': 100, 'point_value': 10, 'tick_size': 1.0, 'name': 'Gold Mini 100g'},
    'GOLDM': {'lot_size': 100, 'point_value': 10, 'tick_size': 1.0, 'name': 'Gold Mini 100g'},
    'MCX:GOLDM': {'lot_size': 100, 'point_value': 10, 'tick_size': 1.0, 'name': 'Gold Mini 100g'},
    'MCX_SILVER': {'lot_size': 30, 'point_value': 30, 'tick_size': 1.0, 'name': 'Silver 30kg'},
    'SILVER': {'lot_size': 30, 'point_value': 30, 'tick_size': 1.0, 'name': 'Silver 30kg'},
    'MCX:SILVER': {'lot_size': 30, 'point_value': 30, 'tick_size': 1.0, 'name': 'Silver 30kg'},
    'MCX_SILVERM': {'lot_size': 5, 'point_value': 5, 'tick_size': 1.0, 'name': 'Silver Mini 5kg'},
    'SILVERM': {'lot_size': 5, 'point_value': 5, 'tick_size': 1.0, 'name': 'Silver Mini 5kg'},
    'MCX:SILVERM': {'lot_size': 5, 'point_value': 5, 'tick_size': 1.0, 'name': 'Silver Mini 5kg'},
}
