# Kinetic Energy Calculator
print("=== Kinetic Energy Tool ===")

# 1. Get the inputs
mass = float(input("Enter mass (kg): "))
velocity = float(input("Enter velocity (m/s): "))

# 2. Calculate KE (0.5 is the same as 1/2)
# In Python, **2 means 'squared'
ke = 0.5 * mass * (velocity ** 2)

# 3. Show the result with a separator
print("-" * 30)
print(f"The Kinetic Energy is: {ke} Joules")
print("-" * 30)

print("Calculation Successful!")
