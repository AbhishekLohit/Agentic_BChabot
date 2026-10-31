Schema for table: restaurants
   - restaurant_id (INTEGER) [PK]
   - name (TEXT)
   - address (TEXT)
   - city (TEXT)
   - type (TEXT)
   - primary_cuisine (TEXT)
   - avg_cost_for_2 (INTEGER)
   - rating (REAL)



Schema for table: orders
   - order_id (TEXT) [PK]
   - customer_id (TEXT)
   - restaurant_id (INTEGER)
   - order_timestamp (TEXT)
   - order_day (TEXT)
   - order_cost (REAL)
   - order_user_rating (REAL)
   - order_delivery_time_mins (INTEGER)
   - order_time_slot (TEXT)
   - order_status (TEXT)
   - ordered_items_count (INTEGER)
   - order_discount_amount (REAL)
   - order_amount_paid (REAL)
   - order_complaint_flag (BOOLEAN)

Schema for table: order_items
   - item_id (INTEGER) [PK]
   - order_id (TEXT)
   - food_item (TEXT)
   - food_item_cost (REAL)